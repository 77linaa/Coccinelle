"""Plain-English explanations of Storm alerts, written by Claude.

Python computes every number; the model only interprets them. It receives
nothing but the computed figures for the alert (level, score, price, spread,
the fictional account's buffer and leverage, the latest forecast and stress
test) — no headlines, no free text from users.

Built so a slow or failing API can never hurt the demo:

- runs in its own task (an alert hook), never on the price path;
- short timeout and no retries: past ``timeout_s`` it gives up;
- falls back to a deterministic template built from the same numbers;
- caches by (pair, level, direction), so a repeat alert is instant and one
  in-flight request is shared by concurrent alerts for the same key;
- no credentials, ``SENTINEL_AI=off`` or a refusal → template.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sentinel.alerts import Alert

logger = logging.getLogger("sentinel.ai")

__all__ = ["MODEL", "Explainer", "Explanation", "template_explanation"]

MODEL = "claude-opus-5-5"
TIMEOUT_S = 8.0
MAX_TOKENS = 2_000  # thinking counts toward this; the answer itself is ~3 sentences

SYSTEM_PROMPT = """\
You are the risk explainer inside Sentinel, an FX risk dashboard. An alert has \
just fired. Explain it to the account holder in at most three short sentences \
of plain English: what changed in the market, what it means for their account, \
and the most relevant protective option.

Rules:
- Use only the numbers in the data. Do not invent figures, causes or news.
- USD/JPY falling means the yen is strengthening against the dollar.
- The account is fictional and for demonstration; the forecast is a model \
estimate, not a prediction. Do not give personalised financial advice.
- No headings, bullet points or markdown. Plain sentences only."""


@dataclass(frozen=True)
class Explanation:
    text: str
    source: str                 # "llm" | "template"
    cached: bool
    as_of: str                  # ts of the numbers the text was written from
    latency_ms: float
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "source": self.source, "cached": self.cached,
                "as_of": self.as_of, "latency_ms": self.latency_ms, "model": self.model}


# ============================================================ facts + template


def build_facts(alert: Alert, context: Mapping[str, Any]) -> dict[str, Any]:
    """The only data the model sees: computed numbers, rounded for readability."""
    facts: dict[str, Any] = {
        "pair": alert.pair,
        "time_utc": alert.ts,
        "storm_level": {"from": alert.prev_level, "to": alert.level},
        "risk_rising": alert.escalation,
        "storm_score_0_100": round(alert.score, 1),
        "mid_price": alert.mid,
    }
    if (spread := context.get("spread_bps")) is not None:
        facts["spread_bps"] = round(spread, 2)
    if (p := context.get("portfolio")) is not None:
        facts["account_fictional"] = {
            "equity_usd": round(p["equity"]),
            "maintenance_margin_usd": round(p["maintenance_margin"]),
            "margin_buffer_usd": round(p["margin_buffer"]),
            "leverage_x": round(p["leverage"], 1) if p.get("leverage") is not None else None,
        }
    if (f := context.get("forecast")) is not None:
        stress_steps = f.get("stress", {}).get("steps", [])
        facts["forecast_next_24h"] = {
            "margin_breach_probability": round(f["breach_probability"], 3),
            "loss_95pct_usd": round(f["loss_p95"]),
            "buffer_if_30_31_july_repeats_usd":
                round(stress_steps[-1]["buffer"]) if stress_steps else None,
        }
    return facts


def template_explanation(facts: Mapping[str, Any]) -> str:
    """Deterministic fallback from the same numbers."""
    pair = facts["pair"]
    lvl = facts["storm_level"]
    rising = facts["risk_rising"]
    pair_label = f"{pair[:3]}/{pair[3:]}"
    parts = [
        f"{pair_label} storm level moved from {lvl['from'] or 'start'} to {lvl['to']} "
        f"(score {facts['storm_score_0_100']:.0f}/100) at {facts['mid_price']:.3f}."
    ]
    if (acct := facts.get("account_fictional")) is not None:
        buffer = acct["margin_buffer_usd"]
        parts.append(
            f"Your margin buffer is {buffer:,} USD"
            + (f" at {acct['leverage_x']}x leverage." if acct.get("leverage_x") else ".")
            + (" The account is already below maintenance margin." if buffer < 0 else "")
        )
    if (fc := facts.get("forecast_next_24h")) is not None:
        stress = fc.get("buffer_if_30_31_july_repeats_usd")
        line = f"Model estimate of a 24-hour margin breach: {fc['margin_breach_probability']:.0%}"
        if stress is not None:
            line += f"; if 30–31 July repeated, the buffer would be {stress:,} USD"
        parts.append(line + ".")
    if rising and lvl["to"] in ("amber", "red"):
        parts.append("Consider reducing the position or hedging with a forward.")
    elif not rising:
        parts.append("Risk is easing, but conditions can turn again quickly.")
    return " ".join(parts)


# ============================================================ explainer


class Explainer:
    """Turns alerts into explanations: Claude when available, template otherwise."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = MODEL,
        timeout_s: float = TIMEOUT_S,
        enabled: bool | None = None,
    ) -> None:
        if enabled is None:
            enabled = os.environ.get("SENTINEL_AI", "on").strip().lower() != "off"
        self.model = model
        self.timeout_s = timeout_s
        self.client = client if client is not None else (_default_client() if enabled else None)
        self.status = "live" if self.client is not None else (
            "template (SENTINEL_AI=off)" if not enabled else "template (no client)")
        self._cache: dict[tuple[str, str, bool], Explanation] = {}
        self._inflight: dict[tuple[str, str, bool], asyncio.Task[Explanation]] = {}

    @property
    def live(self) -> bool:
        return self.client is not None

    async def explain(self, alert: Alert, context: Mapping[str, Any]) -> Explanation:
        key = (alert.pair, alert.level, alert.escalation)
        if (hit := self._cache.get(key)) is not None:
            return Explanation(hit.text, hit.source, True, hit.as_of, 0.0, hit.model)
        if (task := self._inflight.get(key)) is not None:   # share one request
            return await asyncio.shield(task)

        facts = build_facts(alert, context)
        task = asyncio.create_task(self._generate(facts, alert.ts))
        self._inflight[key] = task
        try:
            result = await asyncio.shield(task)
        finally:
            self._inflight.pop(key, None)
        if result.source == "llm":  # only cache real explanations; retry later otherwise
            self._cache[key] = result
        return result

    async def _generate(self, facts: dict[str, Any], as_of: str) -> Explanation:
        t0 = time.perf_counter()
        if self.client is not None:
            try:
                text = await asyncio.wait_for(self._ask(facts), self.timeout_s)
                if text:
                    return Explanation(text, "llm", False, as_of,
                                       (time.perf_counter() - t0) * 1e3, self.model)
            except asyncio.TimeoutError:
                logger.warning("AI explanation timed out after %.1fs; using template",
                               self.timeout_s)
            except Exception as exc:  # any API/network failure → template, never crash
                if _is_auth_error(exc):  # will never succeed: stop trying this session
                    self.client = None
                    self.status = "template (no Anthropic credentials)"
                    logger.warning("AI explanations disabled: no valid Anthropic credentials "
                                   "(set ANTHROPIC_API_KEY); using templates")
                else:
                    logger.warning("AI explanation failed (%s: %s); using template",
                                   type(exc).__name__, exc)
        return Explanation(template_explanation(facts), "template", False, as_of,
                           (time.perf_counter() - t0) * 1e3)

    async def _ask(self, facts: dict[str, Any]) -> str | None:
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user",
                       "content": "Alert data:\n" + json.dumps(facts, indent=2)}],
            timeout=self.timeout_s,
        )
        if response.stop_reason == "refusal":
            logger.warning("AI explanation refused; using template")
            return None
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        return text or None


def _is_auth_error(exc: Exception) -> bool:
    try:
        import anthropic
    except ImportError:
        return False
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    # raised by the SDK before sending when no credential source is configured
    return isinstance(exc, TypeError) and "authentication" in str(exc).lower()


def _default_client() -> Any | None:
    try:
        import anthropic

        # No retries: a retry would blow the timeout; the template covers failures.
        return anthropic.AsyncAnthropic(max_retries=0, timeout=TIMEOUT_S)
    except Exception as exc:  # missing package or no credentials configured
        logger.warning("AI explanations disabled (%s); using templates", exc)
        return None
