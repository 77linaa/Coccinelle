import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sentinel.ai import MODEL, Explainer, build_facts, template_explanation
from sentinel.alerts import Alert
from sentinel.api import create_app

ALERT = Alert(ts="2026-07-30T13:38:00Z", pair="USDJPY", tenor="SPOT", prev_level="amber",
              level="red", score=88.2, mid=161.417, escalation=True)
CONTEXT = {
    "spread_bps": 0.12,
    "portfolio": {"equity": 5_174.4, "maintenance_margin": 3_000.0, "margin_buffer": 2_174.4,
                  "leverage": 19.33, "cash": 6_000.0},
    "forecast": {"breach_probability": 0.0, "loss_p95": 534.2,
                 "stress": {"steps": [{"buffer": 2_174}, {"buffer": 300}, {"buffer": -1_567.4}]}},
}


def reply(text="USD/JPY dropped sharply.", stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)])


class FakeClient:
    """Stands in for anthropic.AsyncAnthropic: records calls, optional delay/error."""

    def __init__(self, response=None, delay_s=0.0, error=None):
        self.calls = []
        self.response, self.delay_s, self.error = response or reply(), delay_s, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay_s)
        if self.error:
            raise self.error
        return self.response


def explain(explainer, alert=ALERT, context=CONTEXT):
    return asyncio.run(explainer.explain(alert, context))


# --------------------------------------------------------------- facts / template


def test_model_sees_only_computed_numbers():
    facts = build_facts(ALERT, CONTEXT)
    assert facts == {
        "pair": "USDJPY", "time_utc": "2026-07-30T13:38:00Z",
        "storm_level": {"from": "amber", "to": "red"}, "risk_rising": True,
        "storm_score_0_100": 88.2, "mid_price": 161.417, "spread_bps": 0.12,
        "account_fictional": {"equity_usd": 5_174, "maintenance_margin_usd": 3_000,
                              "margin_buffer_usd": 2_174, "leverage_x": 19.3},
        "forecast_next_24h": {"margin_breach_probability": 0.0, "loss_95pct_usd": 534,
                              "buffer_if_30_31_july_repeats_usd": -1_567},
    }


def test_template_states_the_numbers_and_an_action():
    text = template_explanation(build_facts(ALERT, CONTEXT))
    for expected in ("USD/JPY", "amber to red", "88/100", "161.417", "2,174 USD",
                     "19.3x", "0%", "-1,567 USD", "hedging"):
        assert expected in text


def test_template_handles_missing_context_and_easing():
    easing = Alert("2026-07-30T14:59:00Z", "USDJPY", "SPOT", "yellow", "green", 23.9,
                   159.3, False)
    text = template_explanation(build_facts(easing, {}))
    assert "yellow to green" in text and "easing" in text


# --------------------------------------------------------------- explainer


def test_llm_request_shape():
    client = FakeClient()
    out = explain(Explainer(client))
    (call,) = client.calls
    assert out.source == "llm" and out.text == "USD/JPY dropped sharply."
    assert out.model == MODEL and not out.cached
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    sent = json.loads(call["messages"][0]["content"].split("\n", 1)[1])
    assert sent == build_facts(ALERT, CONTEXT)                  # numbers only


def test_cached_by_pair_level_and_direction():
    client = FakeClient()
    ex = Explainer(client)
    first = explain(ex)
    again = explain(ex, context={})                            # same key: no new call
    easing = explain(ex, Alert(ALERT.ts, "USDJPY", "SPOT", "red", "red", 80.0, 160.0, False))
    assert len(client.calls) == 2
    assert again.cached and again.text == first.text and again.latency_ms == 0.0
    assert not easing.cached


def test_concurrent_alerts_share_one_request():
    client = FakeClient(delay_s=0.05)
    ex = Explainer(client)

    async def burst():
        return await asyncio.gather(*(ex.explain(ALERT, CONTEXT) for _ in range(5)))

    results = asyncio.run(burst())
    assert len(client.calls) == 1
    assert {r.text for r in results} == {"USD/JPY dropped sharply."}


def test_timeout_falls_back_to_template_quickly():
    ex = Explainer(FakeClient(delay_s=5.0), timeout_s=0.1)
    t0 = time.perf_counter()
    out = explain(ex)
    assert time.perf_counter() - t0 < 1.0
    assert out.source == "template" and "2,174 USD" in out.text


@pytest.mark.parametrize("client", [
    FakeClient(error=RuntimeError("connection reset")),
    FakeClient(response=reply(stop_reason="refusal")),
    FakeClient(response=reply(text="   ")),
])
def test_failures_fall_back_to_template_and_are_not_cached(client):
    ex = Explainer(client)
    assert explain(ex).source == "template"
    explain(ex)
    assert len(client.calls) == 2          # a later alert retries the API


def test_disabled_never_calls_out():
    ex = Explainer(enabled=False)
    assert not ex.live
    assert explain(ex).source == "template"


# --------------------------------------------------------------- API


def storm(levels):
    async def factory(start):
        for i, level in enumerate(levels):
            yield {"ts": f"2026-07-30T13:{i:02d}:00Z", "pair": "USDJPY", "tenor": "SPOT",
                   "mid": 162.0 - i, "spread_bps": 0.5, "score": 90.0 if level == "red" else 5.0,
                   "level": level, "latency_us": 5.0}
            await asyncio.sleep(0.003)
        await asyncio.Event().wait()
    return factory


def test_explanation_pushed_after_alert_without_delaying_ticks():
    slow = Explainer(FakeClient(delay_s=0.3))
    app = create_app(source=storm(["green", "red"] + ["red"] * 30), source_name="test",
                     speed=1_000_000, forecaster=None, explainer=slow, autoplay=False)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/replay/start")
            seen = []
            while not seen or seen[-1]["type"] != "explanation":
                seen.append(ws.receive_json())
            assert client.get("/health").json()["ai"] == "live"
    kinds = [m["type"] for m in seen]
    alert_at = kinds.index("alert")
    assert kinds.count("tick") - kinds[:alert_at].count("tick") >= 20  # stream kept going
    msg = seen[-1]
    assert msg["alert"]["level"] == "red"
    assert msg["explanation"]["source"] == "llm"
    assert msg["explanation"]["as_of"] == "2026-07-30T13:01:00Z"


def test_missing_credentials_switch_to_template_for_the_session():
    no_creds = TypeError("Could not resolve authentication method. Expected one of api_key")
    client = FakeClient(error=no_creds)
    ex = Explainer(client)
    assert ex.status == "live"
    assert explain(ex).source == "template"
    assert explain(ex).source == "template"
    assert len(client.calls) == 1                     # stopped trying after the first
    assert not ex.live and "no Anthropic credentials" in ex.status
