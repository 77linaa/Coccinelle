"""Tests for engine.replay on small synthetic Parquet files."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from engine.replay import Quote, load_frame, replay


def _write(dir_: Path, pair: str, tenor: str, times: list[str]) -> None:
    n = len(times)
    pd.DataFrame({
        "timestamp": pd.to_datetime(times, utc=True),
        "pair": pair, "tenor": tenor,
        "close_bid": [1.0] * n, "close_ask": [1.1] * n, "close_mid": [1.05] * n,
    }).to_parquet(dir_ / f"{pair}_{tenor}.parquet", index=False)


def _setup(tmp_path: Path) -> Path:
    _write(tmp_path, "USDJPY", "SPOT", ["2026-07-01 00:01", "2026-07-01 00:03"])
    _write(tmp_path, "EURUSD", "SPOT", ["2026-07-01 00:01", "2026-07-01 00:02"])
    _write(tmp_path, "EURUSD", "1M", ["2026-07-01 00:04"])
    return tmp_path


def test_merged_stream_is_time_ordered(tmp_path: Path) -> None:
    quotes = list(replay(load_frame(_setup(tmp_path))))
    assert [q.t_ns for q in quotes] == sorted(q.t_ns for q in quotes)
    # Tie at 00:01 broken by pair name: EURUSD before USDJPY.
    assert [(q.pair, q.tenor) for q in quotes[:2]] == [("EURUSD", "SPOT"), ("USDJPY", "SPOT")]
    assert quotes[0].ts == "2026-07-01T00:01:00Z"
    assert isinstance(quotes[0], Quote) and len(quotes) == 5


def test_filters_and_end(tmp_path: Path) -> None:
    d = _setup(tmp_path)
    assert set(load_frame(d, pairs=["EURUSD"])["tenor"]) == {"SPOT", "1M"}
    assert set(load_frame(d, tenors=["SPOT"])["pair"]) == {"EURUSD", "USDJPY"}
    assert len(load_frame(d, end="2026-07-01 00:02")) == 3


def test_replay_is_a_lazy_generator(tmp_path: Path) -> None:
    gen = replay(load_frame(_setup(tmp_path)))
    first = next(gen)  # only one quote produced so far
    assert first.ts == "2026-07-01T00:01:00Z"


def test_speed_paces_the_stream(tmp_path: Path) -> None:
    # 3 minutes of data at 1800x real time should take about 0.1 s.
    t0 = time.perf_counter()
    list(replay(load_frame(_setup(tmp_path)), speed=1800))
    assert 0.08 < time.perf_counter() - t0 < 1.0
