"""Public entry point: the per-quote risk message stream Person B imports.

    from engine import stream
    for msg in stream(pairs=["USDJPY"], tenors=["SPOT"]):
        ...  # {ts, pair, tenor, mid, spread_bps, score, level, latency_us}

latency_us is the engine's own processing time for that quote: from the
moment the quote is handed to the engine until its message is built
(indicator updates + score + message). It excludes the replay feed itself.

CLI demo:  python -m engine --pair USDJPY --tenor SPOT \
               --start 2026-07-30T13:00 --end 2026-07-30T15:00
"""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter_ns
from typing import Iterable, Iterator

import pandas as pd

from engine.replay import DEFAULT_DATA, load_frame, replay
from engine.signals import SignalEngine, level_for


def stream(
    pairs: Iterable[str] | None = None,
    tenors: Iterable[str] | None = None,
    speed: float | None = None,
    start: str | None = None,
    end: str | None = None,
    data_dir: Path = DEFAULT_DATA,
) -> Iterator[dict]:
    """Yield one risk message per quote, in time order.

    Quotes before `start` are still processed (they warm up the
    indicators, as a live system would have seen them) but not yielded.

    Contract with the backend: it passes these arguments BY POSITION, so
    never reorder them; add new parameters only at the end.
    """
    start_ns = pd.Timestamp(start, tz="UTC").value if start else None
    engine = SignalEngine()
    for q in replay(load_frame(data_dir, pairs, tenors, end), speed, start_ns):
        t0 = perf_counter_ns()
        sig = engine.update(q.pair, q.tenor, q.t_ns, q.bid, q.ask, q.mid)
        msg = {
            "ts": q.ts,
            "pair": q.pair,
            "tenor": q.tenor,
            "mid": q.mid,
            "spread_bps": sig.spread_bps,
            "score": sig.score,
            "level": level_for(sig.score),
        }
        msg["latency_us"] = (perf_counter_ns() - t0) / 1000
        if start_ns is None or q.t_ns >= start_ns:
            yield msg


def main() -> None:
    p = argparse.ArgumentParser(description="Print the risk stream.")
    p.add_argument("--pair", action="append", help="repeatable, e.g. USDJPY")
    p.add_argument("--tenor", action="append", help="repeatable, e.g. SPOT")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--speed", type=float, help="x real time (default: max)")
    p.add_argument("--changes", action="store_true",
                   help="print only quotes where the level changes")
    a = p.parse_args()
    last: dict[tuple[str, str], str] = {}
    for m in stream(a.pair, a.tenor, a.speed, a.start, a.end):
        key = (m["pair"], m["tenor"])
        if a.changes and last.get(key) == m["level"]:
            continue
        last[key] = m["level"]
        print(f"{m['ts']}  {m['pair']} {m['tenor']:<4} mid={m['mid']:<10.5f} "
              f"spread={m['spread_bps']:6.2f}bp  score={m['score']:5.1f} "
              f"{m['level']:<6}  {m['latency_us']:5.1f}us")
