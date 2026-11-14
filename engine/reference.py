"""Plain pandas implementation of the same signals, for tests and benchmarks.

It computes every indicator for a whole history at once (vectorised), using
pandas' own ewm/rolling functions. It needs all the data up front, so it
cannot run live, but it is an independent check that the streaming engine
in signals.py computes the right numbers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.signals import (HL_FAST, HL_SLOW, HL_SPREAD, LAM_HOLD,
                            NS_PER_HOUR, RAMPS, STD_EPS, WARMUP, WINDOW_MIN)


def _one_instrument(g: pd.DataFrame) -> pd.DataFrame:
    """Signals for one instrument; g must be sorted by timestamp."""
    mid, bid, ask = g["close_mid"], g["close_bid"], g["close_ask"]
    t_ns = g["timestamp"].astype("int64")

    spread = (ask - bid) / mid * 1e4
    hour = (t_ns // NS_PER_HOUR) % 24
    # Norm for this hour from PREVIOUS quotes of the same hour: shift(1).
    norm = spread.groupby(hour).transform(
        lambda s: s.ewm(halflife=HL_SPREAD).mean().shift(1))
    spread_ratio = (spread / norm).where(norm > 0, 1.0)

    r = np.log(mid / mid.shift(1))
    fast = (r * r).ewm(halflife=HL_FAST).mean()
    slow = (r * r).ewm(halflife=HL_SLOW).mean()
    prev_slow = slow.shift(1)
    jump = (r.abs() / np.sqrt(prev_slow)).where(prev_slow > 0, 0.0)
    vol_ratio = np.sqrt(fast / slow).where(slow > 0, 1.0)

    roll = pd.Series(mid.to_numpy(), index=g["timestamp"]).rolling(f"{WINDOW_MIN}min")
    hi, lo = roll.max().to_numpy(), roll.min().to_numpy()
    mean, std = roll.mean().to_numpy(), roll.std(ddof=0).to_numpy()
    m = mid.to_numpy()
    z = np.where(std > STD_EPS * m, (m - mean) / np.where(std > 0, std, 1), 0.0)
    s = slow.to_numpy()
    move = np.where(s > 0, np.maximum(hi - m, m - lo) / m
                    / (np.sqrt(np.where(s > 0, s, 1)) * np.sqrt(WINDOW_MIN)), 0.0)

    vals = {"vol": vol_ratio.to_numpy(), "move": move, "jump": jump.to_numpy(),
            "z": np.abs(z), "spread": spread_ratio.to_numpy()}
    raw = 100 * sum(w * np.clip((vals[k] - a) / (b - a), 0, 1)
                    for k, (w, a, b) in RAMPS.items())
    raw = np.where(np.arange(len(g)) >= WARMUP, raw, 0.0)

    # Peak hold: score_t = max(raw_t, score_{t-1} * LAM_HOLD). This recursion
    # has no pandas built-in, so it is a plain loop.
    score = np.empty(len(raw))
    prev = 0.0
    for i, x in enumerate(raw):
        prev = score[i] = max(x, prev * LAM_HOLD)

    return pd.DataFrame({"spread_bps": spread.to_numpy(), "raw_score": raw,
                         "score": score}, index=g.index)


def reference_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Signals for a frame of quotes (any number of instruments).

    Returns spread_bps, raw_score and score aligned to df's index.
    """
    parts = [_one_instrument(g.sort_values("timestamp"))
             for _, g in df.groupby(["pair", "tenor"], sort=False)]
    return pd.concat(parts).reindex(df.index)
