"""Contract with Person B's backend (backend/sentinel/feed.py, forecast.py).

If one of these tests fails, the backend will break: talk to Person B
before changing the engine's public interface.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from engine import stream
from engine.replay import load_frame
from engine.tests.test_streaming_vs_pandas import synthetic
from engine.tide_table import ROUGH

MESSAGE_KEYS = {"ts", "pair", "tenor", "mid", "spread_bps", "score", "level",
                "latency_us"}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Synthetic Parquet files laid out like data/processed/."""
    for (pair, tenor), g in synthetic().groupby(["pair", "tenor"]):
        g.to_parquet(tmp_path / f"{pair}_{tenor}.parquet", index=False)
    return tmp_path


def test_stream_argument_order_is_frozen() -> None:
    # feed.py calls stream(pairs, tenors, None, start, end, data_dir) by position.
    params = list(inspect.signature(stream).parameters)
    assert params[:6] == ["pairs", "tenors", "speed", "start", "end", "data_dir"]


def test_load_frame_argument_order_is_frozen() -> None:
    # forecast.py calls load_frame(data_dir, pairs, ["SPOT"]) by position.
    params = list(inspect.signature(load_frame).parameters)
    assert params[:3] == ["data_dir", "pairs", "tenors"]


def test_positional_call_and_message_format(data_dir: Path) -> None:
    msgs = list(stream(["USDJPY"], ["SPOT"], None, "2026-07-02T00:00", None, data_dir))
    assert msgs
    m = msgs[0]
    assert set(m) == MESSAGE_KEYS
    assert isinstance(m["ts"], str) and m["ts"].endswith("Z")
    assert {x["level"] for x in msgs} <= {"green", "yellow", "amber", "red"}
    assert all(0 <= x["score"] <= 100 for x in msgs)
    assert all(x["ts"] >= "2026-07-02T00:00:00Z" for x in msgs)


def test_load_frame_columns(data_dir: Path) -> None:
    df = load_frame(data_dir, ["USDJPY"], ["SPOT"])
    assert {"timestamp", "pair", "close_mid"} <= set(df.columns)
    assert str(df["timestamp"].dt.tz) == "UTC"


def test_warm_up_start_gives_identical_messages(data_dir: Path) -> None:
    # Jumping to `start` must give exactly what a full replay shows from then on.
    start = "2026-07-02T12:00"
    full = [m for m in stream(None, None, None, None, None, data_dir)
            if m["ts"] >= "2026-07-02T12:00:00Z"]
    jumped = list(stream(None, None, None, start, None, data_dir))
    drop = lambda ms: [{k: v for k, v in m.items() if k != "latency_us"} for m in ms]
    assert drop(jumped) == drop(full)


def test_tide_rough_threshold() -> None:
    assert ROUGH == 1.5  # backend forecast.py asserts it uses the same value
