"""Step 6 tests: streaming == pandas, and the future cannot change the past."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.reference import reference_signals
from engine.replay import DEFAULT_DATA, load_frame, replay
from engine.signals import SignalEngine

REAL = DEFAULT_DATA / "USDJPY_SPOT.parquet"


def synthetic(n: int = 4000, seed: int = 0) -> pd.DataFrame:
    """Two instruments of 1-minute quotes with gaps, a storm and spread spikes."""
    rng = np.random.default_rng(seed)
    frames = []
    for pair, p0 in (("USDJPY", 150.0), ("EURUSD", 1.1)):
        t = pd.Timestamp("2026-07-01", tz="UTC") + pd.to_timedelta(
            np.cumsum(rng.choice([1, 2, 90], p=[0.9, 0.08, 0.02], size=n)),
            unit="min")  # mostly 1-min steps, some missing minutes and gaps
        vol, drift = np.full(n, 1e-4), np.zeros(n)
        vol[2500:2600], drift[2500:2600] = 2e-3, -5e-4  # storm: a ~5% slide
        mid = p0 * np.exp(np.cumsum(rng.normal(drift, vol)))
        spread = mid * 1e-5 * (1 + 5 * (t.hour == 21)) * rng.uniform(0.5, 1.5, n)
        frames.append(pd.DataFrame({
            "timestamp": t, "pair": pair, "tenor": "SPOT",
            "close_bid": mid - spread / 2, "close_ask": mid + spread / 2,
            "close_mid": mid}))
    df = pd.concat(frames)
    return df.sort_values(["timestamp", "pair", "tenor"], kind="stable",
                          ignore_index=True)


def run_stream(df: pd.DataFrame) -> pd.DataFrame:
    """Feed quotes one at a time through the streaming engine."""
    eng = SignalEngine()
    out = [eng.update(q.pair, q.tenor, q.t_ns, q.bid, q.ask, q.mid)
           for q in replay(df)]
    return pd.DataFrame({"spread_bps": [s.spread_bps for s in out],
                         "raw_score": [s.raw_score for s in out],
                         "score": [s.score for s in out]})


def assert_match(df: pd.DataFrame) -> None:
    got, ref = run_stream(df), reference_signals(df)
    for col in ("spread_bps", "raw_score", "score"):
        np.testing.assert_allclose(got[col], ref[col], rtol=1e-9, atol=1e-6,
                                   err_msg=col)


def test_streaming_matches_pandas_synthetic() -> None:
    df = synthetic()
    assert_match(df)
    assert run_stream(df)["score"].max() > 75  # the storm is actually detected


@pytest.mark.skipif(not REAL.exists(), reason="real data not processed")
def test_streaming_matches_pandas_real_usdjpy() -> None:
    assert_match(load_frame(pairs=["USDJPY"], tenors=["SPOT"], end="2026-08-05"))


def test_truncating_the_future_changes_no_past_output() -> None:
    df = synthetic()
    full = run_stream(df)
    cut = 3000
    part = run_stream(df.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], part)  # bit-for-bit equal


def test_rewriting_the_future_changes_no_past_output() -> None:
    df = synthetic()
    full = run_stream(df)
    cut = 2500  # just before the storm
    crazy = df.copy()
    crazy.loc[cut:, ["close_bid", "close_ask", "close_mid"]] *= 3.0  # market crash
    changed = run_stream(crazy)
    pd.testing.assert_frame_equal(full.iloc[:cut], changed.iloc[:cut])
    assert not full.iloc[cut:].equals(changed.iloc[cut:])  # future did change


@pytest.mark.skipif(not REAL.exists(), reason="real data not processed")
def test_public_stream_no_lookahead_real() -> None:
    from engine import stream
    a = list(stream(["USDJPY"], ["SPOT"], end="2026-07-30T14:00"))
    b = list(stream(["USDJPY"], ["SPOT"], end="2026-07-30T13:00"))
    key = lambda m: {k: v for k, v in m.items() if k != "latency_us"}
    assert [key(m) for m in a[:len(b)]] == [key(m) for m in b]
    assert set(a[0]) == {"ts", "pair", "tenor", "mid", "spread_bps", "score",
                         "level", "latency_us"}
