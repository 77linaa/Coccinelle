"""Level-change alerts on the fast path.

``AlertTracker.update`` runs on every price: one dict lookup and a comparison.
It returns an ``Alert`` only when an instrument's Storm level changes
(e.g. green → red), and ``None`` otherwise.

Every change is reported, exactly as the engine publishes it. The engine's
peak-hold score keeps flicker low, but a score hovering near a threshold can
still flip (30 July 14:11–14:13: red → amber → red).

The first message seen for an instrument sets its baseline silently, unless it
is already above green — then it alerts (``prev_level=None``), so a replay that
starts or jumps into a storm is flagged immediately.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["LEVEL_RANK", "Alert", "AlertTracker"]

LEVEL_RANK = {"green": 0, "yellow": 1, "amber": 2, "red": 3}


@dataclass(frozen=True, slots=True)
class Alert:
    ts: str
    pair: str
    tenor: str
    prev_level: str | None
    level: str
    score: float
    mid: float
    escalation: bool  # True when risk went up

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AlertTracker:
    """Remembers the last level per (pair, tenor) and reports changes."""

    __slots__ = ("_levels",)

    def __init__(self) -> None:
        self._levels: dict[tuple[str, str], str] = {}

    def reset(self) -> None:
        """Forget all levels (call when the replay restarts or jumps)."""
        self._levels.clear()

    def update(self, event: Mapping[str, Any]) -> Alert | None:
        level = event["level"]
        if level not in LEVEL_RANK:
            raise ValueError(f"Unknown level {level!r}")
        key = (event["pair"], event["tenor"])
        prev = self._levels.get(key)
        if level == prev:
            return None
        self._levels[key] = level
        if prev is None and level == "green":
            return None
        return Alert(
            ts=event["ts"],
            pair=event["pair"],
            tenor=event["tenor"],
            prev_level=prev,
            level=level,
            score=event["score"],
            mid=event["mid"],
            escalation=LEVEL_RANK[level] > LEVEL_RANK.get(prev, 0),
        )
