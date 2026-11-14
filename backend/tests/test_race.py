import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.forecast import Forecaster, compare
from sentinel.hedges import Action, QuoteBook, apply_action
from sentinel.portfolio import Portfolio, Position
from sentinel.race import AlreadyProtected, ProtectionRace

from test_forecast import as_of_after, history_from_returns


def tick(minute, mid, level="green", tenor="SPOT", spread_bps=1.0, hour=13):
    return {"ts": f"2026-07-30T{hour:02d}:{minute:02d}:00Z", "pair": "USDJPY", "tenor": tenor,
            "mid": mid, "spread_bps": spread_bps, "score": 0.0, "level": level,
            "latency_us": 5.0}


# Calm, red at 13:02, keeps falling to a breach for the unprotected account.
STORM = [tick(0, 162.75), tick(1, 162.5), tick(2, 161.4, "red"), tick(3, 160.5, "red"),
         tick(4, 159.5, "red"), tick(5, 158.5, "red"), tick(6, 157.5, "red")]


def account():
    return Portfolio(6_000, [Position("USDJPY", 100_000, 162.75)])


def run(events, race, live, quotes, protect_at=None, action=Action("reduce", percent=50)):
    out = None
    for i, e in enumerate(events):
        quotes.update(e)
        live.on_price(e)
        race.on_event(e, live, quotes)
        if protect_at == i:
            out = race.protect(action, live, quotes)
    return out


# --------------------------------------------------------------- race logic


def test_sentinel_acts_at_first_red_with_the_judges_action():
    race, live, q = ProtectionRace(), account(), QuoteBook()
    run(STORM, race, live, q, protect_at=4)
    s = race.summary(live)

    assert s["red_at"] == "2026-07-30T13:02:00Z"
    assert s["sentinel"]["acted_at"] == "2026-07-30T13:02:00Z"
    assert s["judge"]["acted_at"] == "2026-07-30T13:04:00Z"
    assert s["sentinel_lead_minutes"] == 2.0
    assert s["action"] == {"kind": "reduce", "pair": "USDJPY", "percent": 50.0, "tenor": "1M"}
    assert s["sentinel"]["portfolio"]["equity"] > s["judge"]["portfolio"]["equity"]
    assert not s["unprotected"]["survived"]
    assert s["replay_exact"]


def test_retroactive_sentinel_matches_acting_live_at_red():
    """Replaying from the saved red tick is exact: same as if Sentinel acted live."""
    race, live, q = ProtectionRace(), account(), QuoteBook()
    run(STORM, race, live, q, protect_at=5)
    retro = race.summary(live)["sentinel"]

    live2, q2 = account(), QuoteBook()
    for e in STORM[:3]:
        q2.update(e)
        live2.on_price(e)
    direct = apply_action(live2, Action("reduce", percent=50), q2).portfolio
    worst = direct.snapshot().margin_buffer
    for e in STORM[3:]:
        worst = min(worst, direct.on_price(e).margin_buffer)

    assert retro["portfolio"]["equity"] == pytest.approx(direct.snapshot().equity)
    assert retro["min_buffer"] == pytest.approx(worst)


def test_judge_before_red_then_sentinel_acts_when_red_arrives():
    race, live, q = ProtectionRace(), account(), QuoteBook()
    run(STORM, race, live, q, protect_at=0)
    s = race.summary(live)
    assert s["judge"]["acted_at"] == "2026-07-30T13:00:00Z"
    assert s["sentinel"]["acted_at"] == "2026-07-30T13:02:00Z"
    assert s["sentinel_lead_minutes"] == -2.0          # the judge was faster


def test_no_red_means_no_sentinel_yet():
    race, live, q = ProtectionRace(), account(), QuoteBook()
    run(STORM[:2], race, live, q, protect_at=1)
    s = race.summary(live)
    assert s["red_at"] is None and s["sentinel"] is None and s["judge"] is not None


def test_one_press_per_timeline_until_reset():
    race, live, q = ProtectionRace(), account(), QuoteBook()
    run(STORM, race, live, q, protect_at=3)
    with pytest.raises(AlreadyProtected):
        race.protect(Action("close"), live, q)
    race.reset_protection()
    assert race.protect(Action("close"), live, q)["approved"]
    assert race.summary(live)["red_at"] == "2026-07-30T13:02:00Z"   # timeline kept


def test_rejected_press_records_nothing():
    race, live, q = ProtectionRace(), account(), QuoteBook()
    rollover = [tick(m, 162.0, hour=21, spread_bps=16.4) for m in range(3)]
    out = run(rollover, race, live, q, protect_at=2)
    assert not out["approved"] and "21:00 UTC" in out["reasons"][0]
    assert race.judge is None


def test_sentinel_blocked_by_risk_officer_is_reported():
    race, live, q = ProtectionRace(), account(), QuoteBook()
    events = [tick(0, 162.0, hour=21), tick(1, 161.0, "red", hour=21, spread_bps=16.4),
              tick(0, 160.0, "red", hour=22)]
    run(events, race, live, q, protect_at=2)          # judge at 22:00 is allowed
    s = race.summary(live)
    assert s["judge"] is not None and s["sentinel"] is None
    assert "21:00 UTC" in s["sentinel_blocked"]["reasons"][0]


def test_reset_timeline_forgets_everything():
    race, live, q = ProtectionRace(), account(), QuoteBook()
    run(STORM, race, live, q, protect_at=4)
    race.reset_timeline()
    assert not race.active


# --------------------------------------------------------------- fair forecast


def test_compare_uses_common_random_numbers():
    h = history_from_returns(np.random.default_rng(0).normal(0, 8e-4, 3_000))
    pf = Portfolio(6_000, [Position("USDJPY", 100_000, 155.0)])
    pf.on_price(tick(0, 155.0))
    same = compare(pf, pf.copy(), h, as_of_after(3_000), seed=None)   # unseeded!
    assert same["common_random_numbers"]
    assert same["before"]["cone"] == same["after"]["cone"]
    assert same["before"]["breach_probability"] == same["after"]["breach_probability"]

    q = QuoteBook()
    q.update(tick(0, 155.0))
    reduced = apply_action(pf, Action("reduce", percent=50), q).portfolio
    c = compare(pf, reduced, h, as_of_after(3_000))
    assert c["after"]["breach_probability"] <= c["before"]["breach_probability"]
    assert c["after"]["loss_p95"] < c["before"]["loss_p95"]


# --------------------------------------------------------------- API


def feed(events):
    async def factory(start):
        for e in events:
            yield dict(e)
            await asyncio.sleep(0.003)
        await asyncio.Event().wait()
    return factory


def wait_for(ws, pred):
    while not pred(msg := ws.receive_json()):
        pass
    return msg


def client_for(events, **kw):
    kw.setdefault("forecaster", None)
    app = create_app(source=feed(events), source_name="test", speed=1_000_000,
                     portfolio_factory=account, **kw)
    return TestClient(app)


def test_protect_endpoint_and_tick_protection_field():
    with client_for(STORM) as client:
        with client.websocket_connect("/ws") as ws:
            wait_for(ws, lambda m: m["type"] == "tick" and m["market"]["ts"].endswith("13:04:00Z"))
            out = client.post("/protect", json={"kind": "reduce", "percent": 50}).json()
            assert out["approved"]
            assert wait_for(ws, lambda m: m["type"] == "protection")["protection"]["judge"]
            last = wait_for(ws, lambda m: m["type"] == "tick"
                            and m["market"]["ts"].endswith("13:06:00Z"))
            assert client.post("/protect", json={"kind": "close"}).status_code == 409
            assert client.post("/protect/reset").json()["judge"] is None
    p = last["protection"]
    assert p["sentinel"]["acted_at"].endswith("13:02:00Z")
    assert p["sentinel_lead_minutes"] > 0
    assert p["unprotected"]["survived"] is False


def test_protect_validation_errors():
    with client_for(STORM) as client:
        with client.websocket_connect("/ws") as ws:
            wait_for(ws, lambda m: m["type"] == "tick")
            assert client.post("/protect", json={"kind": "sell"}).status_code == 422
            assert client.post("/protect", json={"kind": "reduce", "percent": 0}).status_code == 422
            hedge = client.post("/protect", json={"kind": "hedge", "tenor": "1M"})
            assert hedge.status_code == 422 and "No live quote" in hedge.json()["detail"]


def test_protect_before_any_price_is_409():
    app = create_app(source=feed(STORM), source_name="test", autoplay=False, forecaster=None)
    with TestClient(app) as client:
        assert client.post("/protect", json={"kind": "close"}).status_code == 409


def test_simulate_previews_without_applying_and_compares_forecasts():
    events = [tick(m, 155.0 - 0.01 * m) for m in range(5)]
    events += [tick(5, 154.9, tenor="1M"), tick(5, 154.95)]
    fc = Forecaster(history_from_returns(np.random.default_rng(1).normal(0, 8e-4, 9_000)),
                    n_paths=300)
    with client_for(events, forecaster=fc, forecast_interval_s=0.02) as client:
        with client.websocket_connect("/ws") as ws:
            wait_for(ws, lambda m: m["type"] == "tick" and m["market"]["ts"].endswith("13:05:00Z"))
            assert client.get("/health").json()["forecast"] == "ready"
            sim = client.post("/simulate", json={"kind": "hedge", "tenor": "1M"}).json()
            assert client.get("/protect").json()["judge"] is None   # nothing applied
    assert sim["review"]["approved"]
    assert sim["cost"] > 0 and "OTC" in sim["label"]
    assert sim["portfolio"]["notional"] == pytest.approx(0.0)
    f = sim["forecast"]
    assert f["common_random_numbers"]
    assert f["after"]["loss_p95"] < f["before"]["loss_p95"]
    assert f["before"]["stress"]["breached"] is not None


def test_worst_buffer_ignores_the_unmarked_opening_state():
    """Forward quotes arrive before spot in the same minute; the account is not
    marked yet, so its entry-price buffer must not count as a real observation."""
    race, live, q = ProtectionRace(), Portfolio(6_000, [Position("USDJPY", 100_000, 155.0)]), QuoteBook()
    run([tick(0, 162.9, tenor="1M"), tick(0, 162.9), tick(1, 162.8)], race, live, q)
    worst = race.summary(live)["unprotected"]["min_buffer"]
    assert worst == pytest.approx(live.snapshot().margin_buffer)
    assert worst > 7_000
