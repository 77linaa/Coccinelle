import json
import math

import pytest

from sentinel.portfolio import Portfolio, Position, demo_portfolio


def msg(mid, pair="USDJPY", tenor="SPOT", ts="2026-07-30T13:30:00Z"):
    return {"ts": ts, "pair": pair, "tenor": tenor, "mid": mid}


# --------------------------------------------------------------- spec example


def test_opening_snapshot_has_full_buffer():
    snap = demo_portfolio().snapshot()
    assert snap.unrealised_pnl == 0.0
    assert snap.equity == 6_000.0
    assert snap.maintenance_margin == pytest.approx(3_000.0)
    assert snap.margin_buffer == pytest.approx(3_000.0)


def test_usdjpy_fall_to_152_04_shrinks_buffer_to_about_1053():
    snap = demo_portfolio().on_price(msg(152.04))

    expected_pnl = 100_000 * (152.04 - 155.00) / 152.04  # JPY -> USD
    assert snap.unrealised_pnl == pytest.approx(expected_pnl)
    assert snap.unrealised_pnl == pytest.approx(-1_946.86, abs=0.01)
    assert snap.equity == pytest.approx(4_053.14, abs=0.01)
    assert snap.maintenance_margin == pytest.approx(3_000.0)
    assert snap.margin_buffer == pytest.approx(1_053.14, abs=0.01)
    assert snap.as_of == "2026-07-30T13:30:00Z"


# --------------------------------------------------------------- filtering


@pytest.mark.parametrize(
    "other",
    [
        msg(140.0, tenor="1W"),        # same pair, forward tenor
        msg(140.0, tenor="3M"),
        msg(1.10, pair="EUR/USD"),     # pair not held
        msg(1.30, pair="USD/SGD"),
    ],
)
def test_ignores_instruments_not_held(other):
    pf = demo_portfolio()
    pf.on_price(msg(152.04))
    before = pf.snapshot()

    assert pf.on_price(other) is None
    assert pf.snapshot() == before


def test_pair_and_tenor_spelling_is_normalised():
    a = demo_portfolio().on_price(msg(152.04, pair="USD/JPY", tenor="SPOT"))
    assert a is not None
    b = demo_portfolio().on_price(msg(152.04, pair="usdjpy", tenor="spot"))
    assert a == b


# --------------------------------------------------------------- statelessness


def test_result_depends_only_on_latest_price_not_history():
    direct = demo_portfolio().on_price(msg(152.04))

    pf = demo_portfolio()
    for mid in (156.0, 150.0, 153.7, 149.2):
        pf.on_price(msg(mid))
    replayed = pf.on_price(msg(152.04))

    assert replayed == direct


# --------------------------------------------------------------- conventions


def test_short_usdjpy_gains_when_price_falls():
    pf = Portfolio(cash=6_000, positions=[Position("USD/JPY", -100_000, 155.00)])
    snap = pf.on_price(msg(152.04))
    assert snap.unrealised_pnl == pytest.approx(1_946.86, abs=0.01)


def test_usd_quoted_pair_pnl_is_already_usd_and_notional_scales_with_mark():
    pf = Portfolio(cash=5_000, positions=[Position("EUR/USD", 100_000, 1.1000)])
    snap = pf.on_price(msg(1.0900, pair="EUR/USD"))

    assert snap.unrealised_pnl == pytest.approx(-1_000.0)
    assert snap.notional == pytest.approx(109_000.0)
    assert snap.maintenance_margin == pytest.approx(3_270.0)
    assert snap.margin_buffer == pytest.approx(5_000 - 1_000 - 3_270)


def test_multi_position_totals_and_independent_marks():
    pf = Portfolio(
        cash=10_000,
        positions=[
            Position("USD/JPY", 100_000, 155.00),
            Position("EUR/USD", -50_000, 1.1000),
        ],
    )
    pf.on_price(msg(152.04))
    snap = pf.on_price(msg(1.0900, pair="EUR/USD"))

    jpy_pnl = 100_000 * (152.04 - 155.00) / 152.04
    eur_pnl = -50_000 * (1.0900 - 1.1000)
    assert snap.unrealised_pnl == pytest.approx(jpy_pnl + eur_pnl)
    assert snap.notional == pytest.approx(100_000 + 50_000 * 1.09)
    assert pf.mark("USD/JPY") == 152.04


# --------------------------------------------------------------- validation


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_rejects_invalid_mid(bad):
    with pytest.raises(ValueError):
        demo_portfolio().on_price(msg(bad))


def test_rejects_non_usd_cross():
    with pytest.raises(ValueError):
        Position("EUR/JPY", 100_000, 160.0)


@pytest.mark.parametrize("kwargs", [{"units": 0}, {"entry_price": 0}, {"pair": "USDJP"}])
def test_rejects_invalid_position(kwargs):
    args = {"pair": "USD/JPY", "units": 100_000, "entry_price": 155.0} | kwargs
    with pytest.raises(ValueError):
        Position(**args)


# --------------------------------------------------------------- wire format


def test_snapshot_serialises_to_json_and_is_labelled_fictional():
    payload = demo_portfolio().on_price(msg(152.04)).to_dict()
    decoded = json.loads(json.dumps(payload))
    assert decoded["fictional"] is True
    assert decoded["currency"] == "USD"
    assert decoded["margin_buffer"] == pytest.approx(1_053.14, abs=0.01)


# --------------------------------------------------------------- hand calculations
#
# Each row worked by hand (USD account, 3% maintenance on net notional per pair):
#
# 1. Long 100k USD/JPY @155.00, mark 152.04, cash 6,000
#    P&L = 100,000 × (152.04 − 155.00) / 152.04 = −296,000 JPY / 152.04 = −1,946.86
#    Equity 4,053.14; notional 100,000 (USD is base); maintenance 3,000; buffer 1,053.14
#    Leverage 100,000 / 4,053.14 = 24.67x
# 2. Short 50k USD/SGD @1.3500, mark 1.3200, cash 2,000
#    P&L = −50,000 × (1.3200 − 1.3500) / 1.3200 = 1,500 SGD / 1.32 = 1,136.36
#    Equity 3,136.36; notional 50,000; maintenance 1,500; buffer 1,636.36; lev 15.94x
# 3. Long 200k EUR/USD @1.1000, mark 1.0850, cash 10,000
#    P&L = 200,000 × (1.0850 − 1.1000) = −3,000 USD (USD is quote: no conversion)
#    Equity 7,000; notional 200,000 × 1.085 = 217,000; maintenance 6,510; buffer 490; lev 31.0x
# 4. Long 100k USD/JPY spot @160 + short 60k USD/JPY 1M @159.9, marks 158 / 157.9, cash 6,000
#    P&L spot = 100,000 × (158 − 160) / 158 = −1,265.82
#    P&L fwd  = −60,000 × (157.9 − 159.9) / 157.9 = +759.97
#    Equity 5,494.15; net notional |100,000 − 60,000| = 40,000; maintenance 1,200
#    buffer 4,294.15; leverage 7.28x

HAND = [
    (6_000, [("USDJPY", 100_000, 155.00, "SPOT", 152.04)],
     dict(unrealised_pnl=-1_946.86, equity=4_053.14, notional=100_000,
          maintenance_margin=3_000, margin_buffer=1_053.14, leverage=24.67)),
    (2_000, [("USDSGD", -50_000, 1.3500, "SPOT", 1.3200)],
     dict(unrealised_pnl=1_136.36, equity=3_136.36, notional=50_000,
          maintenance_margin=1_500, margin_buffer=1_636.36, leverage=15.94)),
    (10_000, [("EURUSD", 200_000, 1.1000, "SPOT", 1.0850)],
     dict(unrealised_pnl=-3_000, equity=7_000, notional=217_000,
          maintenance_margin=6_510, margin_buffer=490, leverage=31.0)),
    (6_000, [("USDJPY", 100_000, 160.0, "SPOT", 158.0), ("USDJPY", -60_000, 159.9, "1M", 157.9)],
     dict(unrealised_pnl=-505.85, equity=5_494.15, notional=40_000,
          maintenance_margin=1_200, margin_buffer=4_294.15, leverage=7.28)),
]


@pytest.mark.parametrize("cash, legs, expected", HAND, ids=["usdjpy", "usdsgd-short",
                                                             "eurusd", "spot+fwd-netting"])
def test_matches_hand_calculation(cash, legs, expected):
    pf = Portfolio(cash, [Position(p, u, e, t) for p, u, e, t, _ in legs])
    for pair, _, _, tenor, mark in legs:
        pf.on_price({"ts": "2026-07-30T14:00:00Z", "pair": pair, "tenor": tenor, "mid": mark})
    snap = pf.snapshot()
    for field, value in expected.items():
        assert getattr(snap, field) == pytest.approx(value, abs=0.01), field
