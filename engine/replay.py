"""Replay historical quotes as a live, time-ordered feed.

All instruments are merged into ONE stream sorted by timestamp (ties broken
by pair, then tenor, so the order is deterministic). The consumer receives
quotes one at a time through a generator: at the moment it handles a quote,
later quotes have not been yielded yet, so lookahead is impossible by design.

`speed` controls pacing: None = as fast as possible, 60 = 60x real time
(one minute of market data per wall-clock second).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

import numpy as np
import pandas as pd

DEFAULT_DATA = Path("data/processed")
COLUMNS = ["timestamp", "pair", "tenor", "close_bid", "close_ask", "close_mid"]
CHUNK = 100_000  # rows converted to Python objects at a time (bounds memory)


class Quote(NamedTuple):
    """One market quote, as a live feed would deliver it."""

    ts: str  # ISO-8601 UTC, e.g. "2026-07-30T13:31:00Z"
    t_ns: int  # same instant as integer nanoseconds since epoch (for maths)
    pair: str
    tenor: str
    bid: float
    ask: float
    mid: float


def load_frame(
    data_dir: Path = DEFAULT_DATA,
    pairs: Iterable[str] | None = None,
    tenors: Iterable[str] | None = None,
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Load the selected instruments' Parquet files, merged and time-sorted."""
    pairs = set(pairs) if pairs else None
    tenors = set(tenors) if tenors else None
    frames = []
    for f in sorted(Path(data_dir).glob("*_*.parquet")):
        pair, tenor = f.stem.split("_", 1)
        if (pairs and pair not in pairs) or (tenors and tenor not in tenors):
            continue
        frames.append(pd.read_parquet(f, columns=COLUMNS))
    if not frames:
        raise FileNotFoundError(f"No matching Parquet files in {data_dir} "
                                "(run python -m engine.data_loader first)")
    df = pd.concat(frames, ignore_index=True)
    # Normalise to nanosecond UTC so int64 conversion is always nanoseconds.
    df["timestamp"] = df["timestamp"].astype("datetime64[ns, UTC]")
    if end is not None:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.sort_values(["timestamp", "pair", "tenor"], kind="stable",
                          ignore_index=True)


def replay(
    df: pd.DataFrame,
    speed: float | None = None,
    pace_from_ns: int | None = None,
    max_sleep: float = 1.0,
) -> Iterator[Quote]:
    """Yield the rows of a time-sorted frame one Quote at a time.

    speed:        None = no pacing; k = k x real time.
    pace_from_ns: start pacing only from this instant (fast-forward before).
    max_sleep:    cap on any single wait, so overnight/weekend gaps are
                  skipped instead of making the demo sit idle for hours.
    """
    wall0: float | None = None
    data0 = 0
    for lo in range(0, len(df), CHUNK):
        part = df.iloc[lo:lo + CHUNK]
        ts64 = part["timestamp"].dt.tz_localize(None).to_numpy()  # UTC, naive
        iso = np.datetime_as_string(ts64, unit="s", timezone="UTC").tolist()
        rows = zip(iso, ts64.astype("int64").tolist(),
                   part["pair"].tolist(), part["tenor"].tolist(),
                   part["close_bid"].tolist(), part["close_ask"].tolist(),
                   part["close_mid"].tolist())
        for row in rows:
            q = Quote(*row)
            if speed and (pace_from_ns is None or q.t_ns >= pace_from_ns):
                if wall0 is None:
                    wall0, data0 = time.perf_counter(), q.t_ns
                wait = (q.t_ns - data0) / 1e9 / speed - (time.perf_counter() - wall0)
                if wait > max_sleep:  # long market gap: jump the clock forward
                    wall0 -= wait - max_sleep
                    wait = max_sleep
                if wait > 0:
                    time.sleep(wait)
            yield q
