"""Judge vs Sentinel: who protects the account first, and what it was worth.

- **Sentinel** reacts at the first red Storm tick on spot.
- **The judge** presses Protect (``POST /protect``) whenever they choose.

Both get the *same* action, so the comparison is fair. Sentinel's red tick
usually comes before the judge chooses an action, so at that tick we save the
portfolio and the live quotes; when the judge acts, the same action is applied
to that saved moment and then replayed through every price since. This is
exact: portfolio value depends only on the latest marks, not on history.

Three accounts are then marked on every price: unprotected (the live account),
judge, and Sentinel — each tracking its worst buffer, the number that decides
whether the account survived.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sentinel.hedges import Action, QuoteBook, apply_action
from sentinel.portfolio import SPOT, Portfolio
from sentinel.replay_control import _ts_seconds
from sentinel.risk_officer import review

__all__ = ["AlreadyProtected", "ProtectionRace"]

MAX_LOG = 200_000  # prices kept after the red tick for Sentinel's retroactive replay


class AlreadyProtected(RuntimeError):
    pass


@dataclass
class _Moment:
    ts: str
    portfolio: Portfolio
    quotes: QuoteBook


class _Branch:
    """A protected copy of the account, marked forward on every price."""

    def __init__(self, who: str, ts: str, result, verdict: dict[str, Any]) -> None:
        self.who = who
        self.acted_at = ts
        self.acted_wall = time.time()
        self.result = result
        self.review = verdict
        self.portfolio: Portfolio = result.portfolio
        snap = self.portfolio.snapshot()
        self.min_buffer, self.min_buffer_ts = snap.margin_buffer, ts

    def mark(self, event: dict[str, Any]) -> None:
        snap = self.portfolio.on_price(event)
        if snap is not None and snap.margin_buffer < self.min_buffer:
            self.min_buffer, self.min_buffer_ts = snap.margin_buffer, event["ts"]

    def to_dict(self, unprotected_equity: float) -> dict[str, Any]:
        snap = self.portfolio.snapshot()
        return {
            "who": self.who,
            "acted_at": self.acted_at,
            "acted_wall": self.acted_wall,
            "cost": self.result.cost,
            "trades": self.result.trades,
            "label": self.result.label,
            "review": self.review,
            "portfolio": snap.to_dict(),
            "min_buffer": self.min_buffer,
            "min_buffer_ts": self.min_buffer_ts,
            "survived": self.min_buffer >= 0,
            "vs_unprotected": snap.equity - unprotected_equity,
        }


class ProtectionRace:
    def __init__(self, pair: str = "USDJPY") -> None:
        self.pair = pair
        self.reset_timeline()

    # ------------------------------------------------------------ lifecycle

    def reset_timeline(self) -> None:
        """New replay timeline (start / jump): forget everything."""
        self.red: _Moment | None = None
        self._log: list[dict[str, Any]] = []
        self._log_truncated = False
        self.unprotected_min: tuple[float, str | None] | None = None
        self.reset_protection()

    def reset_protection(self) -> None:
        """Let the judge try again on the same timeline."""
        self.action: Action | None = None
        self.judge: _Branch | None = None
        self.sentinel: _Branch | None = None
        self.sentinel_blocked: dict[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.red is not None or self.judge is not None

    # ------------------------------------------------------------ fast path

    def on_event(self, event: dict[str, Any], live: Portfolio, quotes: QuoteBook) -> None:
        """Call after the live portfolio and quote book have taken ``event``. O(1)."""
        if live.as_of is not None:  # not before the account has its first real mark
            snap = live.snapshot()
            if self.unprotected_min is None or snap.margin_buffer < self.unprotected_min[0]:
                self.unprotected_min = (snap.margin_buffer, event["ts"])

        if (self.red is None and event["tenor"] == SPOT and event["pair"] == self.pair
                and event["level"] == "red"):
            self.red = _Moment(event["ts"], live.copy(), quotes.copy())
            if self.action is not None:
                self._sentinel_acts()
        elif self.red is not None and self.sentinel is None and self.sentinel_blocked is None:
            if len(self._log) < MAX_LOG:
                self._log.append(event)
            else:
                self._log_truncated = True

        for branch in (self.judge, self.sentinel):
            if branch is not None:
                branch.mark(event)

    # ------------------------------------------------------------ judge

    def protect(self, action: Action, live: Portfolio, quotes: QuoteBook) -> dict[str, Any]:
        """Judge presses Protect now. Raises ValueError if the action cannot be filled."""
        if self.judge is not None:
            raise AlreadyProtected("Already protected on this timeline; reset first")
        ts = live.as_of
        result = apply_action(live, action, quotes)
        verdict = review(action, live, result.portfolio,
                         level=_level(quotes, self.pair), ts=ts)
        if not verdict["approved"]:
            return {**verdict, "protection": self.summary(live)}

        self.action = action
        self.judge = _Branch("judge", ts, result, verdict)
        if self.red is not None:
            self._sentinel_acts()
        return {**verdict, "protection": self.summary(live)}

    def _sentinel_acts(self) -> None:
        """Apply the chosen action at the saved red tick, then replay to now."""
        assert self.red is not None and self.action is not None
        red = self.red
        try:
            result = apply_action(red.portfolio, self.action, red.quotes)
        except ValueError as exc:
            self.sentinel_blocked = {"approved": False, "reasons": [str(exc)]}
            return
        verdict = review(self.action, red.portfolio, result.portfolio,
                         level="red", ts=red.ts)
        if not verdict["approved"]:
            self.sentinel_blocked = verdict
            return
        branch = _Branch("sentinel", red.ts, result, verdict)
        for event in self._log:
            branch.mark(event)
        self._log.clear()
        self.sentinel = branch

    # ------------------------------------------------------------ output

    def summary(self, live: Portfolio) -> dict[str, Any]:
        snap = live.snapshot()
        lead = None
        if self.judge is not None and self.sentinel is not None:
            lead = (_ts_seconds(self.judge.acted_at) - _ts_seconds(self.sentinel.acted_at)) / 60
        return {
            "red_at": self.red.ts if self.red else None,
            "action": self.action.to_dict() if self.action else None,
            "unprotected": {
                "portfolio": snap.to_dict(),
                "min_buffer": self.unprotected_min[0] if self.unprotected_min else None,
                "min_buffer_ts": self.unprotected_min[1] if self.unprotected_min else None,
                "survived": self.unprotected_min is None or self.unprotected_min[0] >= 0,
            },
            "judge": self.judge.to_dict(snap.equity) if self.judge else None,
            "sentinel": self.sentinel.to_dict(snap.equity) if self.sentinel else None,
            "sentinel_blocked": self.sentinel_blocked,
            "sentinel_lead_minutes": lead,   # > 0: Sentinel acted first (market minutes)
            "replay_exact": not self._log_truncated,
        }


def _level(quotes: QuoteBook, pair: str) -> str | None:
    quote = quotes.find(pair, SPOT)
    return quote.level if quote is not None else None
