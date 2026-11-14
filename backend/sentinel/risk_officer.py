"""Deterministic Risk Officer: plain rules that approve or reject a protective action.

    review(action, before, after, level=..., ts=...) -> {"approved": bool, "reasons": [...]}

No model, no AI: every rejection names the rule and the numbers behind it.
Rules only block an action that makes things *worse* on that measure, so the
officer never stops a trade that is genuinely helping a distressed account.

1. Max leverage      — reject if leverage after > MAX_LEVERAGE and higher than before.
2. Immediate breach  — reject if the buffer after < 0 and lower than before.
3. Red level         — reject any trade that increases net exposure while Storm is red.
4. 21:00 UTC rollover — reject forced selling (reduce/close) during the 21:00 UTC
   hour, when USD/JPY spreads widen ~12× (median 0.13 bp all day, 1.54 bp at
   21:00, peak 6.7 bp at 21:04 in the SGX data); suggest a hedge or waiting.
   Not applied if the account is already below maintenance margin.
"""

from __future__ import annotations

import math
from typing import Any

from sentinel.hedges import Action
from sentinel.portfolio import Portfolio

__all__ = ["MAX_LEVERAGE", "ROLLOVER_HOUR_UTC", "review"]

MAX_LEVERAGE = 30.0          # e.g. the ESMA retail cap for major FX pairs
ROLLOVER_HOUR_UTC = 21


def _leverage(p: Portfolio) -> float:
    snap = p.snapshot()
    if snap.notional == 0:
        return 0.0
    return snap.leverage if snap.leverage is not None else math.inf


def _gross_net(p: Portfolio) -> float:
    return sum(abs(v) for v in p.net_exposures().values())


def review(
    action: Action,
    before: Portfolio,
    after: Portfolio,
    *,
    level: str | None,
    ts: str | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    b, a = before.snapshot(), after.snapshot()

    lev_before, lev_after = _leverage(before), _leverage(after)
    if lev_after > MAX_LEVERAGE and lev_after > lev_before:
        reasons.append(f"Leverage would rise to {lev_after:.1f}x, above the "
                       f"{MAX_LEVERAGE:.0f}x limit")

    if a.margin_buffer < 0 and a.margin_buffer < b.margin_buffer:
        reasons.append(f"Account would breach maintenance margin immediately "
                       f"(buffer {a.margin_buffer:,.0f} USD)")

    if level == "red" and _gross_net(after) > _gross_net(before) + 1e-9:
        reasons.append("Storm level is red: trades that increase exposure are blocked")

    hour = int(ts[11:13]) if ts and len(ts) >= 13 else None
    if (hour == ROLLOVER_HOUR_UTC and action.sells_into_market
            and b.margin_buffer >= 0):
        reasons.append("21:00 UTC rollover: spreads widen ~12x, so forced selling now is "
                       "expensive. Hedge with a forward or wait until spreads normalise")

    return {"approved": not reasons, "reasons": reasons}
