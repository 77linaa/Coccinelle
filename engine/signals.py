"""Streaming risk indicators and the Storm Risk Score (0-100).

Every indicator is updated once per quote in constant (amortised) time and
uses only the current and past quotes. Each instrument (pair + tenor) has
its own independent state.

Indicators per quote
--------------------
- return      r = ln(mid / previous mid)
- EWMA vol    fast (half-life 15 quotes = ~15 min) and slow (1 day) EWMA of
              r^2. vol_ratio = fast vol / slow vol ("is it rougher than usual?").
- jump        |r| / slow vol *before* this quote ("how many sigmas was this
              one-minute move?").
- rolling     over the last 60 minutes: high, low (monotonic deques), mean
              and std (running sums).
  z-score     (mid - mean) / std  ("how stretched is the price?")
  move        largest distance from the 60-min high or low, in units of the
              expected 60-minute move (slow vol * sqrt(60)).
- spread      spread_bps / typical spread for this hour of day. The spread
              widens every day at 21:00 UTC; comparing with the hour's own
              norm stops that routine widening from raising false alarms.

Score
-----
Each indicator is mapped to 0..1 with a linear ramp (0 below "normal",
1 at "extreme"), then combined with fixed weights into a raw 0..100 score.
Spread gets a small weight because, in this data, wide spreads did not
predict big moves.

The published score is a "peak hold with decay":
    score_t = max(raw_t, score_{t-1} * LAM_HOLD)
An alarm rises instantly but fades with a 10-minute half-life, so the level
does not flicker red/amber/red minute by minute during a storm (volatility
persists, so a storm one minute ago is still relevant now).
"""

from __future__ import annotations

import math
from collections import deque
from typing import NamedTuple

# ---- Parameters (shared with the pandas reference implementation) ----------
HL_FAST = 15  # quotes (~minutes)
HL_SLOW = 1440  # ~one trading day of minutes
HL_SPREAD = 120  # observations of the same hour (~2 days)
HL_HOLD = 10  # quotes: half-life of the alarm fade
WINDOW_MIN = 60
WINDOW_NS = WINDOW_MIN * 60 * 10**9
WARMUP = 240  # returns per instrument before the score is trusted
NS_PER_HOUR = 3600 * 10**9
STD_EPS = 1e-9  # relative std below this counts as "flat" (z = 0)

LAM_FAST = 0.5 ** (1 / HL_FAST)
LAM_SLOW = 0.5 ** (1 / HL_SLOW)
LAM_SPREAD = 0.5 ** (1 / HL_SPREAD)
LAM_HOLD = 0.5 ** (1 / HL_HOLD)

# (weight, value giving 0, value giving 1) per component
RAMPS = {
    "vol": (0.35, 1.5, 4.0),  # vol_ratio
    "move": (0.30, 3.0, 8.0),  # 60-min move in expected-move units
    "jump": (0.15, 4.0, 10.0),  # sigmas of the 1-minute return
    "z": (0.10, 2.5, 4.0),  # |rolling z-score|
    "spread": (0.10, 2.0, 5.0),  # spread / hour-of-day norm
}
LEVELS = ((75.0, "red"), (50.0, "amber"), (25.0, "yellow"), (0.0, "green"))


class Signal(NamedTuple):
    """All indicator values for one quote (useful for debugging and demos)."""

    spread_bps: float
    ret: float
    vol_ratio: float
    jump: float
    z: float
    move: float
    spread_ratio: float
    raw_score: float  # this quote alone
    score: float  # after peak-hold decay (the published score)


def ramp(x: float, lo: float, hi: float) -> float:
    """0 at or below lo, 1 at or above hi, linear in between."""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


def combine(vol_ratio: float, move: float, jump: float, z: float,
            spread_ratio: float) -> float:
    """Weighted Storm Risk Score in 0..100."""
    vals = {"vol": vol_ratio, "move": move, "jump": jump, "z": abs(z),
            "spread": spread_ratio}
    return 100.0 * sum(w * ramp(vals[k], lo, hi) for k, (w, lo, hi) in RAMPS.items())


def level_for(score: float) -> str:
    """Map a 0-100 score to green / yellow / amber / red."""
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "green"


class Ewma:
    """Exponentially weighted mean, bias-corrected.

    value = sum(lam^i * x_{t-i}) / sum(lam^i). Equivalent to pandas
    `ewm(halflife=..., adjust=True).mean()`, so early values are not
    dragged towards zero by the starting point.
    """

    __slots__ = ("lam", "num", "den")

    def __init__(self, lam: float) -> None:
        self.lam, self.num, self.den = lam, 0.0, 0.0

    def update(self, x: float) -> None:
        self.num = self.lam * self.num + x
        self.den = self.lam * self.den + 1.0

    @property
    def value(self) -> float:
        return self.num / self.den if self.den else 0.0


class RollingWindow:
    """Prices from the last `span_ns`: mean, std, high, low.

    - mean/std from running sums of (price - ref), updated as items enter
      and leave. Subtracting a nearby reference price keeps the sums small
      and avoids floating-point cancellation. Adding and subtracting many
      floats slowly accumulates rounding error, so every RESYNC pushes (and
      whenever the window shrinks to one price) the sums are recomputed
      exactly around a fresh ref: O(window) work every 1000 quotes, which is
      still O(1) amortised.
    - high/low from monotonic deques: the max-deque keeps only prices that
      could still become the window max (each newer, larger price removes
      smaller older ones). Every price enters and leaves each deque at most
      once, so updates are O(1) amortised.
    """

    RESYNC = 1000

    def __init__(self, span_ns: int) -> None:
        self.span = span_ns
        self.items: deque[tuple[int, float]] = deque()
        self.maxq: deque[tuple[int, float]] = deque()
        self.minq: deque[tuple[int, float]] = deque()
        self.ref = 0.0
        self.s1 = 0.0
        self.s2 = 0.0
        self.pushes = 0

    def _resync(self) -> None:
        """Recompute the running sums exactly around the newest price."""
        self.ref = self.items[-1][1]
        self.s1 = self.s2 = 0.0
        for _, x in self.items:
            d = x - self.ref
            self.s1 += d
            self.s2 += d * d

    def push(self, t: int, x: float) -> None:
        """Add a price at time t and evict everything older than t - span."""
        d = x - self.ref
        self.items.append((t, x))
        self.s1 += d
        self.s2 += d * d
        while self.maxq and self.maxq[-1][1] <= x:
            self.maxq.pop()
        self.maxq.append((t, x))
        while self.minq and self.minq[-1][1] >= x:
            self.minq.pop()
        self.minq.append((t, x))

        cutoff = t - self.span  # window is (t - span, t]
        while self.items[0][0] <= cutoff:
            _, old = self.items.popleft()
            d = old - self.ref
            self.s1 -= d
            self.s2 -= d * d
        while self.maxq[0][0] <= cutoff:
            self.maxq.popleft()
        while self.minq[0][0] <= cutoff:
            self.minq.popleft()

        self.pushes += 1
        if len(self.items) == 1 or self.pushes % self.RESYNC == 0:
            self._resync()

    @property
    def high(self) -> float:
        return self.maxq[0][1]

    @property
    def low(self) -> float:
        return self.minq[0][1]

    def mean_std(self) -> tuple[float, float]:
        n = len(self.items)
        m = self.s1 / n
        var = max(self.s2 / n - m * m, 0.0)  # population variance (ddof=0)
        return self.ref + m, math.sqrt(var)


class InstrumentState:
    """All streaming state for one instrument."""

    def __init__(self) -> None:
        self.prev_mid: float | None = None
        self.score = 0.0
        self.n_returns = 0
        self.fast = Ewma(LAM_FAST)
        self.slow = Ewma(LAM_SLOW)
        self.spread_norm = [Ewma(LAM_SPREAD) for _ in range(24)]
        self.window = RollingWindow(WINDOW_NS)

    def update(self, t_ns: int, bid: float, ask: float, mid: float) -> Signal:
        """Process one quote; return its indicators and score."""
        spread_bps = (ask - bid) / mid * 1e4

        # Spread vs this hour's norm. Ratio uses the norm BEFORE this quote.
        norm = self.spread_norm[(t_ns // NS_PER_HOUR) % 24]
        nv = norm.value
        spread_ratio = spread_bps / nv if norm.den and nv > 0 else 1.0
        norm.update(spread_bps)

        self.window.push(t_ns, mid)
        if self.prev_mid is None:  # first quote: no return yet
            self.prev_mid = mid
            return Signal(spread_bps, 0.0, 1.0, 0.0, 0.0, 0.0, spread_ratio, 0.0, 0.0)

        r = math.log(mid / self.prev_mid)
        self.prev_mid = mid
        prev_slow = self.slow.value  # variance known before this return
        jump = abs(r) / math.sqrt(prev_slow) if prev_slow > 0 else 0.0
        self.fast.update(r * r)
        self.slow.update(r * r)
        self.n_returns += 1
        slow = self.slow.value
        vol_ratio = math.sqrt(self.fast.value / slow) if slow > 0 else 1.0

        w = self.window
        mean, std = w.mean_std()
        z = (mid - mean) / std if std > STD_EPS * mid else 0.0
        move = (max(w.high - mid, mid - w.low) / mid
                / (math.sqrt(slow) * math.sqrt(WINDOW_MIN))) if slow > 0 else 0.0

        raw = 0.0
        if self.n_returns >= WARMUP:
            raw = combine(vol_ratio, move, jump, z, spread_ratio)
        self.score = max(raw, self.score * LAM_HOLD)
        return Signal(spread_bps, r, vol_ratio, jump, z, move, spread_ratio,
                      raw, self.score)


class SignalEngine:
    """Routes each quote to its instrument's state."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str], InstrumentState] = {}

    def update(self, pair: str, tenor: str, t_ns: int, bid: float, ask: float,
               mid: float) -> Signal:
        key = (pair, tenor)
        state = self.states.get(key)
        if state is None:
            state = self.states[key] = InstrumentState()
        return state.update(t_ns, bid, ask, mid)
