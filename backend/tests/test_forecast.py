import asyncio
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.feed import DATA_DIR
from sentinel.forecast import (
    HORIZON_STEPS,
    NS_PER_HOUR,
    NS_PER_MIN,
    ROUGH,
    Forecaster,
    InsufficientHistory,
    ReturnHistory,
    margin_forecast,
    stress_test,
    tide_multipliers,
    to_ns,
)
from sentinel.portfolio import Portfolio, Position, demo_portfolio

T0 = to_ns("2026-07-01T00:00:00Z")
STEP = 5 * NS_PER_MIN


def history_from_returns(returns, start_ns=T0, pairs=("USDJPY",), mid0=160.0):
    """ReturnHistory whose 5-min returns are exactly `returns` (rows × pairs)."""
    r = np.asarray(returns, dtype=float).reshape(len(returns), -1)
    t = start_ns + np.arange(len(r) + 1) * STEP
    mids = mid0 * np.exp(np.vstack([np.zeros(r.shape[1]), np.cumsum(r, axis=0)]))
    return ReturnHistory.from_mids(pairs, [t] * r.shape[1], list(mids.T))


def as_of_after(n_rows):
    t = T0 + n_rows * STEP
    return str(np.datetime64(t, "ns").astype("datetime64[s]")) + "Z"


def marked(portfolio, mid, ts="2026-07-30T13:25:00Z", pair="USDJPY"):
    portfolio.on_price({"ts": ts, "pair": pair, "tenor": "SPOT", "mid": mid})
    return portfolio


# --------------------------------------------------------------- portfolio what-if


def test_revalue_matches_live_path_and_is_pure():
    pf = demo_portfolio()
    before = pf.snapshot()
    equity, maint = pf.revalue({("USDJPY", "SPOT"): 152.04})
    assert pf.snapshot() == before
    live = marked(demo_portfolio(), 152.04).snapshot()
    assert equity == pytest.approx(live.equity)
    assert maint == pytest.approx(live.maintenance_margin)


def test_revalue_accepts_arrays():
    marks = np.array([[155.0, 152.04], [150.0, 160.0]])
    equity, maint = demo_portfolio().revalue({("USDJPY", "SPOT"): marks})
    assert equity.shape == (2, 2)
    assert equity[0, 1] - maint == pytest.approx(1_053.14, abs=0.01)


def test_copy_is_independent():
    pf = marked(demo_portfolio(), 152.04)
    clone = pf.copy()
    marked(pf, 140.0)
    assert clone.snapshot().margin_buffer == pytest.approx(1_053.14, abs=0.01)
    assert clone.as_of == "2026-07-30T13:25:00Z"


# --------------------------------------------------------------- stress test


def test_stress_30_july_from_155_reproduces_worked_example_then_breaches():
    result = stress_test(demo_portfolio())
    now, day1, day2 = result["steps"]
    assert now["buffer"] == pytest.approx(3_000.0)
    assert day1["mark"] == pytest.approx(155.0 * (1 - 0.0191))      # = 152.04
    assert day1["buffer"] == pytest.approx(1_053, abs=1)
    assert day2["mark"] == pytest.approx(155.0 * 0.9809 * 0.9830)
    assert day2["buffer"] < 0 and day2["breached"]
    assert result["breached"] is True


def test_stress_is_a_gain_for_a_short_and_leaves_portfolio_untouched():
    pf = Portfolio(6_000, [Position("USDJPY", -100_000, 155.0)])
    before = pf.snapshot()
    result = stress_test(pf)
    assert result["steps"][-1]["buffer"] > result["steps"][0]["buffer"]
    assert not result["breached"]
    assert pf.snapshot() == before


# --------------------------------------------------------------- history


def test_history_uses_exact_grid_and_drops_gaps():
    t = T0 + np.array([0, 1, 5, 10, 15, 30, 35]) * NS_PER_MIN  # 1-min tick off grid; 15→30 gap
    mids = np.array([100, 999, 101, 102, 103, 110, 111.0])
    h = ReturnHistory.from_mids(["USDJPY"], [t], [mids])
    assert (h.t_ns - T0).tolist() == [x * NS_PER_MIN for x in (5, 10, 15, 35)]
    assert h.returns[:, 0] == pytest.approx(np.log([101 / 100, 102 / 101, 103 / 102, 111 / 110]))


def test_history_aligns_pairs_on_common_times():
    ta = T0 + np.array([0, 5, 10, 15]) * NS_PER_MIN
    tb = T0 + np.array([5, 10, 15, 20]) * NS_PER_MIN
    h = ReturnHistory.from_mids(["A", "B"], [ta, tb], [np.arange(1, 5.0), np.arange(1, 5.0)])
    assert (h.t_ns - T0).tolist() == [10 * NS_PER_MIN, 15 * NS_PER_MIN]
    assert h.returns.shape == (2, 2)


def test_window_only_returns_rows_ending_at_or_before_as_of():
    h = history_from_returns(np.zeros(100))
    t, _ = h.window(T0 + 50 * STEP, lookback_ns=None)
    assert t[-1] == T0 + 50 * STEP and len(t) == 50
    t, _ = h.window(T0 + 50 * STEP, lookback_ns=10 * STEP)
    assert len(t) == 10


# --------------------------------------------------------------- tide multipliers


def test_tide_multiplier_scales_only_rough_hours():
    rng = np.random.default_rng(1)
    n = 12 * 24 * 20                                    # 20 days of 5-min returns
    t = T0 + (np.arange(n) + 1) * STEP
    r = rng.choice([-1.0, 1.0], size=n) * 1e-4
    hour = ((t - STEP) // NS_PER_HOUR) % 24
    r[hour == 13] *= 4                                  # 13:00 UTC is stormy
    mult = tide_multipliers(t, r[:, None])
    assert mult[13, 0] >= ROUGH
    assert mult[13, 0] == pytest.approx(4 / ((23 + 4) / 24), rel=1e-6)
    assert np.all(np.delete(mult[:, 0], 13) == 1.0)


def test_rough_threshold_matches_engine_tide_table():
    tide_table = pytest.importorskip("engine.tide_table")
    assert ROUGH == tide_table.ROUGH


# --------------------------------------------------------------- Monte Carlo


def test_zero_volatility_gives_zero_breach_and_flat_cone():
    h = history_from_returns(np.zeros(1_000))
    pf = marked(demo_portfolio(), 155.0)
    f = margin_forecast(pf, h, as_of_after(1_000), n_paths=200)
    assert f["breach_probability"] == 0.0
    assert f["loss_p95"] == pytest.approx(0.0, abs=1e-6)
    assert f["cone"]["p5"] == f["cone"]["p95"] == [6_000.0] * HORIZON_STEPS


def test_steady_decline_breaches_every_long_path_and_no_short_path():
    h = history_from_returns(np.full(1_000, -0.001))   # −0.1% every 5 min
    long_ = margin_forecast(marked(demo_portfolio(), 155.0), h, as_of_after(1_000), n_paths=200)
    short = margin_forecast(
        marked(Portfolio(6_000, [Position("USDJPY", -100_000, 155.0)]), 155.0),
        h, as_of_after(1_000), n_paths=200)
    assert long_["breach_probability"] == 1.0
    # buffer hits 0 when 100k × (m − 155)/m = −3,000  →  ln(155/m) = ln(1.03)
    steps_to_breach = int(np.ceil(np.log(1.03) / 0.001))             # 30 steps
    assert long_["median_hours_to_breach"] == pytest.approx(steps_to_breach * 5 / 60)
    assert short["breach_probability"] == 0.0


def test_already_breached_portfolio_is_certain():
    h = history_from_returns(np.zeros(1_000))
    f = margin_forecast(marked(demo_portfolio(), 145.0), h, as_of_after(1_000), n_paths=100)
    assert f["buffer_now"] < 0
    assert f["breach_probability"] == 1.0


def test_shapes_ordering_and_reproducibility():
    rng = np.random.default_rng(0)
    h = history_from_returns(rng.normal(0, 8e-4, 3_000))
    pf = marked(demo_portfolio(), 155.0)
    a = margin_forecast(pf, h, as_of_after(3_000), seed=7)
    b = margin_forecast(pf, h, as_of_after(3_000), seed=7)
    c = margin_forecast(pf, h, as_of_after(3_000), seed=8)
    cone = np.array([a["cone"][f"p{p}"] for p in (5, 25, 50, 75, 95)])
    assert cone.shape == (5, HORIZON_STEPS)
    assert np.all(np.diff(cone, axis=0) >= 0)
    assert 0.0 < a["breach_probability"] < 1.0
    assert a["breach_probability"] == b["breach_probability"]
    assert a["cone"] == b["cone"]
    assert a["cone"] != c["cone"]


def test_insufficient_history_refuses():
    h = history_from_returns(np.zeros(100))
    with pytest.raises(InsufficientHistory):
        margin_forecast(demo_portfolio(), h, as_of_after(100))


def test_no_lookahead_future_returns_change_nothing():
    """Same as_of, history with vs without a crash afterwards: identical forecast."""
    rng = np.random.default_rng(3)
    past = rng.normal(0, 5e-4, 2_000)
    future_crash = np.full(500, -0.01)
    full = history_from_returns(np.concatenate([past, future_crash]))
    truncated = history_from_returns(past)
    pf = marked(demo_portfolio(), 155.0)
    as_of = as_of_after(2_000)

    with_future = margin_forecast(pf, full, as_of)
    without = margin_forecast(pf, truncated, as_of)
    for key in ("breach_probability", "loss_p95", "cone", "median_hours_to_breach"):
        assert with_future[key] == without[key]
    assert with_future["model"]["history_to"] == as_of


@pytest.mark.skipif(not (DATA_DIR / "USDJPY_SPOT.parquet").exists(),
                    reason="run `python -m engine.data_loader` first")
def test_real_data_30_july_risk_rises_after_the_fall():
    history = ReturnHistory.load(["USDJPY"])
    fc = Forecaster(history)
    pf = Portfolio(6_000, [Position("USDJPY", 100_000, 162.75)])

    before = fc.run(marked(pf, 162.753, "2026-07-30T13:25:00Z").copy(), "2026-07-30T13:25:00Z")
    t0 = time.perf_counter()
    after = fc.run(marked(pf, 159.237, "2026-07-30T14:30:00Z").copy(), "2026-07-30T14:30:00Z")
    elapsed = time.perf_counter() - t0

    assert before["model"]["history_to"] == "2026-07-30T13:25:00Z"
    assert after["breach_probability"] > before["breach_probability"]
    assert before["stress"]["breached"]        # the warning existed before the fall
    assert elapsed < 1.0


# --------------------------------------------------------------- API integration


class SlowForecaster(Forecaster):
    def __init__(self, history, delay_s):
        super().__init__(history, n_paths=100)
        self.delay_s = delay_s
        self.calls = []

    def run(self, portfolio, as_of):
        self.calls.append(as_of)
        time.sleep(self.delay_s)  # blocks a worker thread, never the event loop
        return super().run(portfolio, as_of)


def minute_feed(n):
    async def factory(start):
        for i in range(n):
            yield {"ts": f"2026-07-30T13:{i:02d}:00Z", "pair": "USDJPY", "tenor": "SPOT",
                   "mid": 155.0 - 0.1 * i, "spread_bps": 0.5, "score": 10.0,
                   "level": "green", "latency_us": 5.0}
        await asyncio.Event().wait()
    return factory


def next_of(ws, kind):
    while (msg := ws.receive_json())["type"] != kind:
        pass
    return msg


def test_forecast_is_pushed_and_slow_forecast_never_delays_ticks():
    fc = SlowForecaster(history_from_returns(np.random.default_rng(0).normal(0, 5e-4, 9_000)),
                        delay_s=0.5)
    app = create_app(source=minute_feed(40), source_name="test", speed=60 * 50,
                     forecaster=fc, forecast_interval_s=0.05)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            t0 = time.monotonic()
            msgs = []
            while not any(m["type"] == "forecast" for m in msgs):
                msgs.append(ws.receive_json())
            ticks_before_forecast = sum(m["type"] == "tick" for m in msgs)
            forecast = next(m for m in msgs if m["type"] == "forecast")
            assert client.get("/forecast").json() == forecast
            assert client.get("/health").json()["forecast"] == "ready"
        with client.websocket_connect("/ws") as ws2:
            assert ws2.receive_json()["forecast"]["as_of"] is not None

    # ticks kept flowing (one every 20 ms) while the 0.5 s forecast ran in a thread
    assert ticks_before_forecast >= 10
    assert time.monotonic() - t0 < 5
    f = forecast["forecast"]
    assert 0.0 <= f["breach_probability"] <= 1.0
    assert f["stress"]["scenario"] == "30-31 July 2026 repeat"
    assert forecast["as_of"] == f["as_of"]


def test_no_recompute_while_paused():
    fc = SlowForecaster(history_from_returns(np.zeros(9_000)), delay_s=0.0)
    app = create_app(source=minute_feed(3), source_name="test", speed=1_000_000,
                     forecaster=fc, forecast_interval_s=0.02)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            next_of(ws, "forecast")
            time.sleep(0.3)  # feed idles after 3 ticks: as_of no longer changes
    assert len(fc.calls) == len(set(fc.calls))


def test_insufficient_history_pushes_null_forecast_with_reason():
    fc = Forecaster(history_from_returns(np.zeros(10)))
    app = create_app(source=minute_feed(3), source_name="test", speed=1_000_000,
                     forecaster=fc, forecast_interval_s=0.02)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msg = next_of(ws, "forecast")
    assert msg["forecast"] is None
    assert "need at least" in msg["reason"]


@pytest.mark.skipif(not (DATA_DIR / "USDJPY_SPOT.parquet").exists(),
                    reason="run `python -m engine.data_loader` first")
@pytest.mark.parametrize("as_of, mid", [("2026-07-30T13:25:00Z", 162.753),
                                        ("2026-07-30T14:30:00Z", 159.237)])
def test_no_lookahead_on_real_data_deleting_future_rows_changes_nothing(as_of, mid):
    """Load the real SGX quotes, delete every row after as_of, forecast again:
    breach probability, cone, losses, tide hours and stress test must be identical."""
    from engine.replay import load_frame

    df = load_frame(DATA_DIR, ["USDJPY"], ["SPOT"])
    t = df["timestamp"].dt.tz_localize(None).to_numpy().astype(np.int64)
    m = df["close_mid"].to_numpy()
    keep = t <= to_ns(as_of)
    assert (~keep).sum() > 10_000                       # there really is future data to delete

    full = ReturnHistory.from_mids(["USDJPY"], [t], [m])
    past_only = ReturnHistory.from_mids(["USDJPY"], [t[keep]], [m[keep]])
    pf = marked(Portfolio(6_000, [Position("USDJPY", 100_000, 162.75)]), mid, ts=as_of)

    a, b = Forecaster(full).run(pf.copy(), as_of), Forecaster(past_only).run(pf.copy(), as_of)
    for key in ("breach_probability", "loss_p95", "median_hours_to_breach", "cone", "model",
                "stress", "buffer_now"):
        assert a[key] == b[key], key
