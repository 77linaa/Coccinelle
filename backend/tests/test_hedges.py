import pytest

from sentinel.hedges import Action, Quote, QuoteBook, apply_action
from sentinel.portfolio import Portfolio, Position
from sentinel.risk_officer import MAX_LEVERAGE, review

TS = "2026-07-30T13:38:00Z"


def quote(mid, spread_bps=1.0, tenor="SPOT", pair="USDJPY", level="red", ts=TS):
    return {"ts": ts, "pair": pair, "tenor": tenor, "mid": mid,
            "spread_bps": spread_bps, "level": level}


def book(*events):
    q = QuoteBook()
    for e in events:
        q.update(e)
    return q


def marked(pf, mid, ts=TS):
    pf.on_price(quote(mid, ts=ts))
    return pf


def long_100k(entry=162.75, cash=6_000):
    return Portfolio(cash, [Position("USDJPY", 100_000, entry)])


# --------------------------------------------------------------- quotes


def test_bid_ask_rebuilt_exactly_from_mid_and_spread():
    q = Quote(TS, "USDJPY", "SPOT", mid=160.0, spread_bps=1.0)
    assert q.ask - q.bid == pytest.approx(160.0 * 1e-4)
    assert (q.bid + q.ask) / 2 == pytest.approx(160.0)
    assert q.fill(+1) == q.ask and q.fill(-1) == q.bid


def test_quote_book_requires_a_quote():
    with pytest.raises(ValueError, match="No live quote"):
        QuoteBook().get("USDJPY", "1M")


# --------------------------------------------------------------- actions


def test_close_long_fills_at_bid_and_realises_pnl():
    pf = marked(long_100k(), 160.0)
    q = book(quote(160.0, spread_bps=1.0))
    res = apply_action(pf, Action("close"), q)

    bid = 160.0 - 0.008
    realised = 100_000 * (bid - 162.75) / bid
    assert res.portfolio.positions == ()
    assert res.portfolio.cash == pytest.approx(6_000 + realised)
    assert res.trades == [{"ts": TS, "pair": "USDJPY", "tenor": "SPOT", "side": "sell",
                           "units": 100_000, "price": pytest.approx(bid), "mid": 160.0,
                           "spread_bps": 1.0}]
    # cost = equity at mid − equity after the fill: half-spread on 100k, plus the
    # JPY loss converted to USD at the bid instead of the mid
    equity_at_mid = 6_000 + 100_000 * (160.0 - 162.75) / 160.0
    assert res.cost == pytest.approx(equity_at_mid - (6_000 + realised))
    assert res.cost == pytest.approx(5.09, abs=0.01)


def test_close_short_fills_at_ask():
    pf = Portfolio(6_000, [Position("USDJPY", -100_000, 162.75)])
    marked(pf, 160.0)
    res = apply_action(pf, Action("close"), book(quote(160.0)))
    assert res.trades[0]["side"] == "buy"
    assert res.trades[0]["price"] == pytest.approx(160.008)


def test_reduce_scales_position_keeps_entry_and_input_untouched():
    pf = marked(long_100k(), 160.0)
    before = pf.snapshot()
    res = apply_action(pf, Action("reduce", percent=40), book(quote(160.0)))
    (pos,) = res.portfolio.positions
    assert pos.units == pytest.approx(60_000) and pos.entry_price == 162.75
    assert pf.snapshot() == before
    assert res.portfolio.snapshot().maintenance_margin == pytest.approx(0.03 * 60_000)
    assert res.portfolio.mark("USDJPY") == 160.0


def test_cost_scales_with_spread():
    pf = marked(long_100k(), 160.0)
    calm = apply_action(pf, Action("close"), book(quote(160.0, spread_bps=0.13)))
    rollover = apply_action(pf, Action("close"), book(quote(160.0, spread_bps=16.4)))
    assert rollover.cost / calm.cost == pytest.approx(16.4 / 0.13, rel=1e-3)


def test_forward_hedge_sells_at_forward_bid_nets_margin_and_is_labelled_otc():
    pf = marked(long_100k(), 160.0)
    q = book(quote(160.0), quote(159.99, spread_bps=0.6, tenor="1M"))
    res = apply_action(pf, Action("hedge", percent=100, tenor="1M"), q)

    spot, fwd = res.portfolio.positions
    assert spot.units == 100_000
    assert (fwd.tenor, fwd.units) == ("1M", -100_000)
    assert fwd.entry_price == pytest.approx(q.get("USDJPY", "1M").bid)
    snap = res.portfolio.snapshot()
    assert snap.notional == pytest.approx(0.0)        # net margining: fully offset
    assert snap.leverage == pytest.approx(0.0)
    assert "OTC" in res.label and "not an SGX-listed future" in res.label
    assert res.cost > 0


def test_hedge_locks_in_jpy_pnl_leaving_only_its_conversion_exposure():
    pf = marked(long_100k(), 160.0)
    q = book(quote(160.0), quote(160.0, spread_bps=0.0, tenor="1M"))
    hedged = apply_action(pf, Action("hedge", tenor="1M"), q).portfolio
    for p in (hedged, pf):
        p.on_price(quote(150.0))
    hedged.on_price(quote(150.0, tenor="1M"))

    locked_jpy = 100_000 * (160.0 - 162.75)            # −275,000 JPY, fixed by the hedge
    assert hedged.snapshot().equity == pytest.approx(6_000 + locked_jpy / 150.0)
    move_hedged = hedged.snapshot().equity - (6_000 + locked_jpy / 160.0)
    move_unhedged = pf.snapshot().equity - (6_000 + locked_jpy / 160.0)
    assert abs(move_hedged) < 0.02 * abs(move_unhedged)


def test_partial_hedge_ratio():
    pf = marked(long_100k(), 160.0)
    q = book(quote(160.0), quote(160.0, tenor="3M"))
    res = apply_action(pf, Action("hedge", percent=25, tenor="3M"), q)
    assert res.portfolio.positions[-1].units == pytest.approx(-25_000)
    assert res.portfolio.snapshot().notional == pytest.approx(75_000)


@pytest.mark.parametrize("action, quotes, match", [
    (Action("hedge", tenor="1M"), [quote(160.0)], "No live quote for USDJPY 1M"),
    (Action("close", pair="EURUSD"), [quote(1.1, pair="EURUSD")], "No open EURUSD"),
])
def test_actions_that_cannot_be_filled(action, quotes, match):
    with pytest.raises(ValueError, match=match):
        apply_action(marked(long_100k(), 160.0), action, book(*quotes))


@pytest.mark.parametrize("kwargs", [{"kind": "buy"}, {"kind": "reduce", "percent": 0},
                                    {"kind": "reduce", "percent": 150},
                                    {"kind": "hedge", "tenor": "1Y"}])
def test_invalid_actions(kwargs):
    with pytest.raises(ValueError):
        Action(**kwargs)


# --------------------------------------------------------------- Risk Officer


def run_review(pf, action, q, level="green", ts=TS):
    res = apply_action(pf, action, q)
    return review(action, pf, res.portfolio, level=level, ts=ts)


def test_protective_action_in_red_is_approved():
    pf = marked(long_100k(), 160.0)
    assert run_review(pf, Action("reduce", percent=50), book(quote(160.0)), level="red") == \
        {"approved": True, "reasons": []}


def test_exposure_increasing_trade_blocked_in_red_but_allowed_in_green():
    pf = marked(long_100k(), 160.0)
    q = book(quote(160.0), quote(160.0, tenor="1M"))
    hedged = apply_action(pf, Action("hedge", tenor="1M"), q).portfolio
    # going from hedged back to fully exposed increases net exposure
    red = review(Action("reduce", percent=50), hedged, pf, level="red", ts=TS)
    green = review(Action("reduce", percent=50), hedged, pf, level="green", ts=TS)
    assert not red["approved"] and "red" in red["reasons"][0]
    assert green["approved"]


def test_leverage_limit_blocks_only_increases():
    high = Portfolio(4_000, [Position("USDJPY", 100_000, 160.0)])  # 25x notional, buffer > 0
    higher = Portfolio(3_200, [Position("USDJPY", 100_000, 160.0)])  # ~31x
    up = review(Action("reduce", percent=50), high, higher, level="green", ts=TS)
    down = review(Action("reduce", percent=50), higher, high, level="green", ts=TS)
    assert not up["approved"] and f"{MAX_LEVERAGE:.0f}x" in up["reasons"][0]
    assert down["approved"]


def test_immediate_breach_blocked_unless_it_improves_things():
    healthy = Portfolio(6_000, [])
    breached = Portfolio(2_000, [Position("USDJPY", 100_000, 160.0)])     # buffer −1,000
    worse = review(Action("reduce", percent=50), healthy, breached, level="green", ts=TS)
    better = review(Action("reduce", percent=50), breached, healthy, level="green", ts=TS)
    assert not worse["approved"]
    assert any("breach maintenance margin" in r for r in worse["reasons"])
    assert better["approved"]


def test_no_forced_selling_at_2100_utc_but_hedging_is_fine():
    ts = "2026-07-30T21:04:00Z"
    pf = marked(long_100k(), 160.0, ts=ts)
    q = book(quote(160.0, spread_bps=16.4, ts=ts),
             quote(160.0, spread_bps=16.4, tenor="1M", ts=ts))
    sell = run_review(pf, Action("reduce", percent=50), q, ts=ts)
    hedge = run_review(pf, Action("hedge", tenor="1M"), q, ts=ts)
    at_20 = run_review(pf, Action("reduce", percent=50), q, ts="2026-07-30T20:59:00Z")
    assert not sell["approved"] and "21:00 UTC" in sell["reasons"][0]
    assert hedge["approved"]
    assert at_20["approved"]


def test_2100_rule_yields_when_account_already_breached():
    ts = "2026-07-30T21:04:00Z"
    pf = marked(Portfolio(2_000, [Position("USDJPY", 100_000, 160.0)]), 160.0, ts=ts)
    assert run_review(pf, Action("reduce", percent=50), book(quote(160.0, ts=ts)),
                      ts=ts)["approved"]


# --------------------------------------------------------------- more bid/ask checks


def test_hedging_a_short_buys_the_forward_at_the_ask():
    pf = Portfolio(6_000, [Position("USDJPY", -100_000, 162.75)])
    marked(pf, 160.0)
    q = book(quote(160.0), quote(159.99, spread_bps=0.6, tenor="1M"))
    res = apply_action(pf, Action("hedge", tenor="1M"), q)
    fwd = res.portfolio.positions[-1]
    assert fwd.units == 100_000 and res.trades[0]["side"] == "buy"
    assert fwd.entry_price == pytest.approx(159.99 * (1 + 0.6 / 2e4))      # the ask


def test_partial_reduce_cost_by_hand():
    # 40k of 100k long closed at bid = 160 − 160×1bp/2 = 159.992
    # realised = 40,000 × (159.992 − 162.75) / 159.992 = −689.53 USD
    # at mid the same slice is worth 40,000 × (160 − 162.75) / 160 = −687.50 USD
    # cost = 2.03 USD (half-spread 0.008 JPY × 40k = 320 JPY ≈ 2.00, plus conversion)
    pf = marked(long_100k(), 160.0)
    res = apply_action(pf, Action("reduce", percent=40), book(quote(160.0, spread_bps=1.0)))
    assert res.trades[0]["price"] == pytest.approx(159.992)
    assert res.portfolio.cash == pytest.approx(6_000 - 689.53, abs=0.01)
    assert res.cost == pytest.approx(689.53 - 687.50, abs=0.01)


@pytest.mark.parametrize("ts, blocked", [
    ("2026-07-30T20:59:00Z", False),
    ("2026-07-30T21:00:00Z", True),
    ("2026-07-30T21:59:00Z", True),
    ("2026-07-30T22:00:00Z", False),
])
def test_2100_rule_covers_exactly_the_21_utc_hour(ts, blocked):
    pf = marked(long_100k(), 160.0, ts=ts)
    verdict = run_review(pf, Action("close"), book(quote(160.0, ts=ts)), ts=ts)
    assert verdict["approved"] is not blocked
