"""Margin forecast: a deterministic stress test and a bootstrap Monte Carlo.

1. Stress test — "what if 30 July happens again?"
   Applies the real USD/JPY daily moves of 30 and 31 July 2026 (−1.91%, then
   −1.70%) to the current portfolio and reports the buffer after each day.

2. Bootstrap Monte Carlo — "how likely is a margin breach in the next 24 h?"
   - History: 5-minute log returns of each held pair's spot mid, taken on an
     exact 5-minute grid. Rows are resampled whole, so pairs keep their
     correlation and the real jumps are kept (no normal-distribution smoothing).
   - Tide table: hours whose historical volatility is ≥ ROUGH × normal have
     their sampled returns multiplied by that hour's volatility index.
   - 2,000 paths × 288 steps (24 h) in one NumPy array. Each path is valued
     with ``Portfolio.revalue`` — the same formulas as the live path.
   - A path breaches if its buffer (equity − maintenance) drops below 0 at any
     step. Probability = breaching paths / paths. The cone is the percentiles
     of equity at each step.

No lookahead: everything is computed from returns ending at or before
``as_of``. The return ending at grid time g needs only prices up to g, and the
tide multipliers are re-derived from that same history (``engine.tide_table``
summarises the whole dataset, including the future, so it is not used here).

These are model-based estimates under stated assumptions, not predictions.
The process is CPU-bound: run it off the event loop (``run_in_executor``).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sentinel.feed import DATA_DIR
from sentinel.portfolio import Portfolio

__all__ = [
    "JULY_30_31",
    "ROUGH",
    "Forecaster",
    "InsufficientHistory",
    "ReturnHistory",
    "Scenarios",
    "compare",
    "evaluate",
    "margin_forecast",
    "simulate",
    "stress_test",
    "to_ns",
]

NS_PER_MIN = 60 * 10**9
NS_PER_HOUR = 60 * NS_PER_MIN
NS_PER_DAY = 24 * NS_PER_HOUR

STEP_MINUTES = 5
HORIZON_STEPS = 288               # 24 h of 5-minute steps
N_PATHS = 2_000
LOOKBACK_DAYS = 30
MIN_RETURNS = 500                 # refuse to forecast from less history than this
ROUGH = 1.5                       # same threshold as engine.tide_table.ROUGH
MIN_HOUR_SAMPLES = 30             # hours with fewer returns get multiplier 1
PERCENTILES = (5, 25, 50, 75, 95)

# Real USD/JPY daily moves (see project brief): 30 July then 31 July 2026.
JULY_30_31: tuple[tuple[str, float], ...] = (("30 Jul", -0.0191), ("31 Jul", -0.0170))


class InsufficientHistory(RuntimeError):
    """Not enough past returns before ``as_of`` to forecast honestly."""


def to_ns(ts: str | np.datetime64 | int) -> int:
    """'2026-07-30T13:25:00Z' (UTC) → integer nanoseconds since epoch."""
    if isinstance(ts, (int, np.integer)):
        return int(ts)
    if isinstance(ts, str):
        ts = ts.rstrip("Z")
    return int(np.datetime64(ts, "ns").astype(np.int64))


# ============================================================ history


@dataclass(frozen=True)
class ReturnHistory:
    """Aligned 5-minute log returns for several pairs.

    ``t_ns[i]`` is the grid time at which return row ``i`` *ends* (sorted).
    ``returns[i, j]`` is pair ``pairs[j]``'s log return over (t − 5 min, t].
    Only rows where every pair has both grid prices exactly 5 minutes apart are
    kept, so overnight/weekend gaps never show up as fake jumps.
    """

    pairs: tuple[str, ...]
    t_ns: np.ndarray
    returns: np.ndarray

    @classmethod
    def from_mids(
        cls, pairs: Sequence[str], times: Sequence[np.ndarray], mids: Sequence[np.ndarray],
        step_minutes: int = STEP_MINUTES,
    ) -> ReturnHistory:
        """Build from per-pair arrays of (UTC ns timestamps, mid prices)."""
        step_ns = step_minutes * NS_PER_MIN
        common: np.ndarray | None = None
        per_pair: list[tuple[np.ndarray, np.ndarray]] = []
        for t, mid in zip(times, mids):
            t = np.asarray(t, dtype=np.int64)
            mid = np.asarray(mid, dtype=np.float64)
            on_grid = t % step_ns == 0
            t, mid = t[on_grid], mid[on_grid]
            order = np.argsort(t, kind="stable")
            t, mid = t[order], mid[order]
            ok = np.diff(t) == step_ns
            t_end, r = t[1:][ok], np.diff(np.log(mid))[ok]
            per_pair.append((t_end, r))
            common = t_end if common is None else np.intersect1d(common, t_end)
        if common is None:
            raise ValueError("No pairs given")
        cols = [r[np.searchsorted(t_end, common)] for t_end, r in per_pair]
        return cls(tuple(pairs), common, np.column_stack(cols) if cols else np.empty((0, 0)))

    @classmethod
    def load(cls, pairs: Iterable[str], data_dir: Path = DATA_DIR) -> ReturnHistory:
        """Load spot mids from the engine's Parquet files."""
        from engine.replay import load_frame  # lazy: pandas only when used

        pairs = tuple(sorted(set(pairs)))
        df = load_frame(data_dir, pairs, ["SPOT"])
        groups = {p: g for p, g in df.groupby("pair", sort=False)}
        missing = set(pairs) - set(groups)
        if missing:
            raise FileNotFoundError(f"No spot data for {sorted(missing)} in {data_dir}")
        times = [groups[p]["timestamp"].dt.tz_localize(None).to_numpy().astype(np.int64)
                 for p in pairs]
        mids = [groups[p]["close_mid"].to_numpy() for p in pairs]
        return cls.from_mids(pairs, times, mids)

    def window(self, as_of_ns: int, lookback_ns: int | None) -> tuple[np.ndarray, np.ndarray]:
        """Rows ending at or before ``as_of`` (and within the lookback)."""
        hi = np.searchsorted(self.t_ns, as_of_ns, side="right")
        lo = 0 if lookback_ns is None else np.searchsorted(self.t_ns, as_of_ns - lookback_ns,
                                                           side="right")
        return self.t_ns[lo:hi], self.returns[lo:hi]


def tide_multipliers(t_ns: np.ndarray, returns: np.ndarray,
                     step_minutes: int = STEP_MINUTES) -> np.ndarray:
    """(24, n_pairs) volatility multipliers by UTC hour, from past returns only.

    vol_index = mean |return| in that hour / overall mean |return| (the engine's
    tide-table definition). Rough hours (≥ ROUGH) get their index; others 1.
    """
    hours = ((t_ns - step_minutes * NS_PER_MIN) // NS_PER_HOUR) % 24  # hour the step starts in
    counts = np.bincount(hours, minlength=24)
    mult = np.ones((24, returns.shape[1]))
    overall = np.abs(returns).mean(axis=0)
    for j in range(returns.shape[1]):
        if overall[j] <= 0:
            continue
        by_hour = np.bincount(hours, weights=np.abs(returns[:, j]), minlength=24)
        with np.errstate(invalid="ignore", divide="ignore"):
            index = by_hour / counts / overall[j]
        rough = (counts >= MIN_HOUR_SAMPLES) & (index >= ROUGH)
        mult[rough, j] = index[rough]
    return mult


# ============================================================ stress test


def stress_test(
    portfolio: Portfolio,
    shocks: Sequence[tuple[str, float]] = JULY_30_31,
    pair: str = "USDJPY",
) -> dict[str, Any]:
    """Apply successive moves to ``pair`` (all tenors held) and report the buffer."""
    marks = {k: v for k, v in portfolio.marks().items() if k[0] == pair}
    equity, maint = portfolio.revalue({})
    steps = [{"label": "now", "move": 0.0, "mark": _first(marks), "equity": equity,
              "buffer": equity - maint, "breached": equity - maint < 0}]
    for label, move in shocks:
        marks = {k: v * (1.0 + move) for k, v in marks.items()}
        equity, maint = portfolio.revalue(marks)
        steps.append({"label": label, "move": move, "mark": _first(marks),
                      "equity": equity, "buffer": equity - maint,
                      "breached": equity - maint < 0})
    return {
        "scenario": "30-31 July 2026 repeat",
        "pair": pair,
        "steps": steps,
        "breached": any(s["breached"] for s in steps),
    }


def _first(marks: dict[Any, float]) -> float | None:
    return next(iter(marks.values()), None)


# ============================================================ Monte Carlo


@dataclass(frozen=True)
class Scenarios:
    """Simulated market paths, shared by every portfolio evaluated on them.

    ``growth[p, s, j]`` is the price multiple of ``pairs[j]`` after step ``s``
    on path ``p``. Evaluating two portfolios on the same ``Scenarios`` is the
    common-random-numbers technique: any difference comes from the portfolios,
    not from simulation noise.
    """

    pairs: tuple[str, ...]
    growth: np.ndarray
    step_minutes: int
    model: dict[str, Any]


def simulate(
    history: ReturnHistory,
    as_of: str | int,
    *,
    n_paths: int = N_PATHS,
    steps: int = HORIZON_STEPS,
    step_minutes: int = STEP_MINUTES,
    lookback_days: float | None = LOOKBACK_DAYS,
    seed: int | None = 0,
) -> Scenarios:
    """Bootstrap ``n_paths`` × ``steps`` price paths from returns before ``as_of``."""
    as_of_ns = to_ns(as_of)
    step_ns = step_minutes * NS_PER_MIN
    lookback_ns = None if lookback_days is None else int(lookback_days * NS_PER_DAY)

    t_hist, r_hist = history.window(as_of_ns, lookback_ns)
    if len(r_hist) < MIN_RETURNS:
        raise InsufficientHistory(
            f"{len(r_hist)} returns before {as_of}; need at least {MIN_RETURNS}")
    mult = tide_multipliers(t_hist, r_hist, step_minutes)            # (24, k)

    rng = np.random.default_rng(seed)
    rows = rng.integers(0, len(r_hist), size=(n_paths, steps))      # (P, S)
    step_hours = ((as_of_ns + np.arange(steps) * step_ns) // NS_PER_HOUR) % 24
    sim = r_hist[rows] * mult[step_hours]                            # (P, S, k)
    return Scenarios(
        pairs=history.pairs,
        growth=np.exp(np.cumsum(sim, axis=1)),
        step_minutes=step_minutes,
        model={
            "method": "bootstrap of real 5-min returns, tide-hour vol scaling",
            "history_returns": int(len(r_hist)),
            "history_from": _iso(t_hist[0]),
            "history_to": _iso(t_hist[-1]),
            "rough_hours_utc": sorted(int(h) for h in np.flatnonzero((mult > 1).any(axis=1))),
            "seed": seed,
        },
    )


def evaluate(portfolio: Portfolio, scenarios: Scenarios) -> dict[str, Any]:
    """Breach probability, losses and equity cone of ``portfolio`` on ``scenarios``."""
    n_paths, steps = scenarios.growth.shape[:2]
    col = {p: j for j, p in enumerate(scenarios.pairs)}
    current = portfolio.marks()
    path_marks = {inst: current[inst] * scenarios.growth[:, :, col[inst[0]]]
                  for inst in current}
    equity, maint = portfolio.revalue(path_marks)                    # (P, S)
    equity = np.broadcast_to(equity, (n_paths, steps))              # flat if nothing held
    buffer = equity - maint

    equity_now, maint_now = portfolio.revalue({})
    buffer_now = equity_now - maint_now
    below = buffer < 0
    ever_below = below.any(axis=1)
    breached = ever_below | (buffer_now < 0)
    first_breach_step = np.argmax(below, axis=1)[ever_below]

    cone = np.percentile(equity, PERCENTILES, axis=0)
    step_minutes = scenarios.step_minutes
    return {
        "horizon_hours": steps * step_minutes / 60,
        "paths": n_paths,
        "breach_probability": float(breached.mean()),
        "equity_now": float(equity_now),
        "maintenance_now": float(maint_now),
        "buffer_now": float(buffer_now),
        "loss_p95": float(equity_now - np.percentile(equity[:, -1], 5)),
        "median_hours_to_breach": (
            round(float((np.median(first_breach_step) + 1) * step_minutes / 60), 2)
            if len(first_breach_step) else None),
        "cone": {
            "step_minutes": step_minutes,
            **{f"p{p}": np.round(row, 2).tolist() for p, row in zip(PERCENTILES, cone)},
        },
        "model": scenarios.model,
    }


def margin_forecast(
    portfolio: Portfolio,
    history: ReturnHistory,
    as_of: str | int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Bootstrap 24-hour margin-breach probability and equity cone at ``as_of``."""
    t0 = time.perf_counter()
    result = {"as_of": as_of if isinstance(as_of, str) else None,
              **evaluate(portfolio, simulate(history, as_of, **kwargs))}
    result["compute_ms"] = (time.perf_counter() - t0) * 1e3
    return result


def compare(
    before: Portfolio,
    after: Portfolio,
    history: ReturnHistory,
    as_of: str | int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Unprotected vs protected forecast on the *same* simulated paths."""
    t0 = time.perf_counter()
    scenarios = simulate(history, as_of, **kwargs)
    result = {
        "as_of": as_of if isinstance(as_of, str) else None,
        "common_random_numbers": True,
        "before": evaluate(before, scenarios),
        "after": evaluate(after, scenarios),
    }
    result["compute_ms"] = (time.perf_counter() - t0) * 1e3
    return result


def _iso(t_ns: np.integer) -> str:
    return str(np.datetime64(int(t_ns), "ns").astype("datetime64[s]")) + "Z"


# ============================================================ runner


@dataclass
class Forecaster:
    """Bundles history + settings; ``run`` is what the API calls in an executor."""

    history: ReturnHistory
    n_paths: int = N_PATHS
    steps: int = HORIZON_STEPS
    lookback_days: float | None = LOOKBACK_DAYS
    seed: int | None = 0  # common random numbers: changes reflect the market, not noise

    def _settings(self) -> dict[str, Any]:
        return {"n_paths": self.n_paths, "steps": self.steps,
                "lookback_days": self.lookback_days, "seed": self.seed}

    def run(self, portfolio: Portfolio, as_of: str) -> dict[str, Any]:
        result = margin_forecast(portfolio, self.history, as_of, **self._settings())
        result["stress"] = stress_test(portfolio)
        return result

    def compare(self, before: Portfolio, after: Portfolio, as_of: str) -> dict[str, Any]:
        result = compare(before, after, self.history, as_of, **self._settings())
        result["before"]["stress"] = stress_test(before)
        result["after"]["stress"] = stress_test(after)
        return result
