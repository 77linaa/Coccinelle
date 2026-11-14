"""Benchmark: streaming engine vs plain pandas.

Three measurements, all on the real data:

1. Streaming engine (engine.stream): every quote, all instruments.
   - throughput = quotes / wall-clock seconds (includes the replay feed)
   - latency    = per-quote engine time (latency_us): median and p99
2. pandas batch (engine.reference): the same signals vectorised over the
   whole history at once. Fast in bulk, but it needs ALL data up front, so
   it cannot react to a new quote: it is not a live system.
3. pandas "live": the naive way to run pandas on a feed, recomputing the
   signals on recent history every time a quote arrives. Measured on a
   sample of quotes with a 1-day trailing window (much shorter than the
   real history, which favours pandas).

It also checks that the streaming and pandas-batch scores agree.

Run:  python -m engine.bench [--pair USDJPY ...] [--live-samples 200]
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from engine.reference import reference_signals
from engine.replay import load_frame
from engine.risk_stream import stream

LIVE_WINDOW = 1440  # rows of trailing history for the naive pandas loop


def bench_stream(pairs: list[str] | None) -> tuple[int, float, np.ndarray, np.ndarray]:
    """Run the streaming engine; return (n, seconds, latencies_us, scores)."""
    lat, scores = [], []
    t0 = time.perf_counter()
    for m in stream(pairs):
        lat.append(m["latency_us"])
        scores.append(m["score"])
    return len(lat), time.perf_counter() - t0, np.array(lat), np.array(scores)


def bench_pandas_batch(df: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    t0 = time.perf_counter()
    ref = reference_signals(df)
    return time.perf_counter() - t0, ref


def bench_pandas_live(df: pd.DataFrame, samples: int) -> np.ndarray:
    """Per-quote latency (us) of recomputing pandas signals on each new quote."""
    one = df[(df["pair"] == "USDJPY") & (df["tenor"] == "SPOT")]
    if one.empty:
        one = df[(df["pair"] == df["pair"].iloc[0]) & (df["tenor"] == "SPOT")]
    one = one.reset_index(drop=True)
    start = len(one) // 2
    lat = []
    for i in range(start, start + samples):
        t0 = time.perf_counter_ns()
        reference_signals(one.iloc[i - LIVE_WINDOW:i + 1]).iloc[-1]
        lat.append((time.perf_counter_ns() - t0) / 1000)
    return np.array(lat)


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark streaming vs pandas.")
    p.add_argument("--pair", action="append", help="limit to pairs (repeatable)")
    p.add_argument("--live-samples", type=int, default=200)
    a = p.parse_args()

    print("1) Streaming engine ...", flush=True)
    n, secs, lat, scores = bench_stream(a.pair)

    print("2) pandas batch ...", flush=True)
    df = load_frame(pairs=a.pair)
    b_secs, ref = bench_pandas_batch(df)

    print(f"3) pandas live ({a.live_samples} quotes) ...", flush=True)
    live = bench_pandas_live(df, a.live_samples)

    diff = np.abs(scores - ref["score"].to_numpy())
    print(f"\n=== Results: {n:,} quotes, {df.groupby(['pair', 'tenor']).ngroups} instruments ===")
    print(f"Streaming engine : {n / secs:>12,.0f} quotes/s  ({secs:.1f} s total)")
    print(f"  latency/quote  : median {np.median(lat):.1f} us, p99 {np.percentile(lat, 99):.1f} us, "
          f"max {lat.max():.0f} us")
    print(f"pandas batch     : {n / b_secs:>12,.0f} quotes/s  ({b_secs:.1f} s total) "
          "- needs all data up front, not live")
    print(f"pandas live      : {1e6 / np.median(live):>12,.0f} quotes/s  "
          f"(median {np.median(live):,.0f} us, p99 {np.percentile(live, 99):,.0f} us per quote, "
          f"{LIVE_WINDOW}-row window)")
    print(f"  -> streaming is {np.median(live) / np.median(lat):,.0f}x faster per quote "
          "than live pandas")
    print(f"Agreement        : max |score diff| streaming vs pandas = {diff.max():.2e}")


if __name__ == "__main__":
    main()
