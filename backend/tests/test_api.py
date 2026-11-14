import asyncio

import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_app

SPOT_152 = {
    "ts": "2026-07-30T13:30:00Z", "pair": "USDJPY", "tenor": "SPOT",
    "mid": 152.04, "spread_bps": 0.6, "score": 80.0, "level": "red", "latency_us": 5.0,
}
EURUSD_SPOT = SPOT_152 | {"pair": "EURUSD", "mid": 1.10}


def repeating(*events, interval_s=0.005):
    """Endless factory cycling through fixed events (same ts, so no pacing)."""

    async def factory(start):
        while True:
            for event in events:
                yield dict(event)
                await asyncio.sleep(interval_s)

    return factory


def app_with(*events, interval_s=0.005, **kwargs):
    kwargs.setdefault("forecaster", None)
    return create_app(source=repeating(*events, interval_s=interval_s), source_name="test", **kwargs)


def next_of(ws, kind):
    while (msg := ws.receive_json())["type"] != kind:
        pass
    return msg


# --------------------------------------------------------------- websocket


def test_hello_carries_replay_state_and_portfolio():
    with TestClient(app_with(SPOT_152)) as client:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
    assert hello["type"] == "hello"
    assert hello["source"] == "test"
    assert hello["replay"]["status"] == "playing"
    assert hello["portfolio"]["fictional"] is True


def test_tick_envelope_portfolio_and_latency():
    with TestClient(app_with(SPOT_152)) as client:
        with client.websocket_connect("/ws") as ws:
            tick = next_of(ws, "tick")

    assert tick["market"] == SPOT_152
    assert tick["portfolio"]["margin_buffer"] == pytest.approx(1_053.14, abs=0.01)
    assert tick["portfolio"]["as_of"] == SPOT_152["ts"]
    assert isinstance(tick["backend_ms"], float)
    assert 0.0 <= tick["backend_ms"] < 50.0


def test_instrument_not_held_sends_null_portfolio():
    with TestClient(app_with(EURUSD_SPOT)) as client:
        with client.websocket_connect("/ws") as ws:
            tick = next_of(ws, "tick")
    assert tick["market"]["pair"] == "EURUSD"
    assert tick["portfolio"] is None


def test_forward_quotes_are_not_broadcast_as_ticks():
    forward = SPOT_152 | {"tenor": "1M", "mid": 152.03}
    with TestClient(app_with(forward, SPOT_152)) as client:
        with client.websocket_connect("/ws") as ws:
            tenors = {next_of(ws, "tick")["market"]["tenor"] for _ in range(5)}
    assert tenors == {"SPOT"}


def test_broadcast_reaches_every_client():
    with TestClient(app_with(SPOT_152)) as client:
        with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
            assert next_of(a, "tick")["market"]["mid"] == 152.04
            assert next_of(b, "tick")["market"]["mid"] == 152.04


def test_malformed_event_is_skipped_not_fatal():
    bad = SPOT_152 | {"mid": -1}
    with TestClient(app_with(bad, SPOT_152)) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(3):
                assert next_of(ws, "tick")["market"]["mid"] == 152.04


def test_health_reports_connected_clients():
    with TestClient(app_with(SPOT_152)) as client:
        assert client.get("/health").json()["clients"] == 0
        with client.websocket_connect("/ws") as ws:
            next_of(ws, "tick")
            body = client.get("/health").json()
    assert body == {"status": "ok", "source": "test", "clients": 1, "forecast": "off",
                    "ai": "template (SENTINEL_AI=off)"}


# --------------------------------------------------------------- replay controls


def test_autoplay_off_starts_idle_until_start():
    with TestClient(app_with(SPOT_152, autoplay=False)) as client:
        assert client.get("/replay").json()["status"] == "idle"
        assert client.post("/replay/start").json()["status"] == "playing"


def test_controls_return_state_and_broadcast_it():
    with TestClient(app_with(SPOT_152, speed=60, start="2026-07-30T13:00")) as client:
        with client.websocket_connect("/ws") as ws:
            assert client.post("/replay/pause").json()["status"] == "paused"
            assert next_of(ws, "replay")["replay"]["status"] == "paused"

            state = client.post("/replay/speed", params={"value": 600}).json()
            assert state["speed"] == 600.0
            assert next_of(ws, "replay")["replay"]["speed"] == 600.0

            state = client.post("/replay/jump", params={"date": "2026-07-30T13:25"}).json()
            assert state["start"] == "2026-07-30T13:25"
            assert state["status"] == "paused"

            assert client.post("/replay/start").json()["status"] == "playing"


@pytest.mark.parametrize("value", [0, -5, 10_000_000, "fast"])
def test_speed_rejects_out_of_range(value):
    with TestClient(app_with(SPOT_152)) as client:
        assert client.post("/replay/speed", params={"value": value}).status_code == 422


@pytest.mark.parametrize("params", [{"date": "not-a-date"}, {}])
def test_jump_rejects_bad_date(params):
    with TestClient(app_with(SPOT_152)) as client:
        assert client.post("/replay/jump", params=params).status_code == 422


def test_source_selected_from_environment(monkeypatch):
    monkeypatch.setenv("SENTINEL_SOURCE", "mock")
    with TestClient(create_app(forecaster=None)) as client:
        assert client.get("/health").json()["source"] == "mock"

    monkeypatch.setenv("SENTINEL_SOURCE", "bogus")
    with pytest.raises(ValueError):
        create_app(forecaster=None)


# --------------------------------------------------------------- alerts


def storm_source(levels):
    """Factory: one event per minute with the given levels, then idles."""

    async def factory(start):
        for i, level in enumerate(levels):
            yield SPOT_152 | {"ts": f"2026-07-30T13:{i:02d}:00Z", "level": level}
            await asyncio.sleep(0.005)
        await asyncio.Event().wait()

    return factory


def test_level_change_emits_alert_before_its_tick():
    app = create_app(source=storm_source(["green", "green", "red", "red"]),
                     source_name="test", speed=1_000_000, autoplay=False,
                     forecaster=None)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            client.post("/replay/start")
            kinds = []
            while len([k for k in kinds if k[0] == "tick"]) < 4:
                m = ws.receive_json()
                if m["type"] == "alert":
                    alert_msg = m
                if m["type"] in ("alert", "tick"):
                    ts = (m.get("alert") or m.get("market"))["ts"]
                    kinds.append((m["type"], ts[11:16]))

    assert kinds == [("tick", "13:00"), ("tick", "13:01"),
                     ("alert", "13:02"), ("tick", "13:02"), ("tick", "13:03")]
    assert alert_msg["alert"]["prev_level"] == "green"
    assert alert_msg["alert"]["level"] == "red"
    assert alert_msg["alert"]["escalation"] is True
    assert 0.0 <= alert_msg["backend_ms"] < 50.0


def test_slow_alert_hook_does_not_delay_the_stream():
    calls = []

    async def slow_hook(alert):
        calls.append(alert.level)
        await asyncio.sleep(10)  # e.g. an LLM call

    app = create_app(source=storm_source(["green", "red", "amber", "red", "green"]),
                     source_name="test", speed=1_000_000, autoplay=False,
                     alert_hooks=[slow_hook], forecaster=None)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/replay/start")
            alerts = [next_of(ws, "alert")["alert"]["level"] for _ in range(4)]

    assert alerts == ["red", "amber", "red", "green"]
    assert calls == alerts


def test_jump_resets_alert_baseline():
    app = create_app(source=storm_source(["red"]), source_name="test",
                     speed=1_000_000, autoplay=False, forecaster=None)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/replay/start")
            first = next_of(ws, "alert")["alert"]
            client.post("/replay/jump", params={"date": "2026-07-30"})
            second = next_of(ws, "alert")["alert"]
    assert first["prev_level"] is None and first["level"] == "red"
    assert second["prev_level"] is None and second["level"] == "red"


# --------------------------------------------------------------- browser access


def test_cors_allows_the_vite_dashboard_to_use_replay_controls():
    origin = "http://localhost:5173"
    with TestClient(app_with(SPOT_152)) as client:
        pre = client.options("/replay/pause", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        post = client.post("/replay/pause", headers={"Origin": origin})
        other = client.post("/replay/start", headers={"Origin": "http://evil.example"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == origin
    assert post.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in other.headers


def test_messages_arrive_in_production_order_under_concurrent_broadcasts():
    """Ticks (replay task) and replay updates (HTTP handlers) race; order must hold."""
    with TestClient(app_with(SPOT_152, interval_s=0)) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            for speed in (100, 200, 300):
                client.post("/replay/speed", params={"value": speed})
            speeds = [next_of(ws, "replay")["replay"]["speed"] for _ in range(3)]
    assert speeds == [100.0, 200.0, 300.0]
