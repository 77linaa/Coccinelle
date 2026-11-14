"""Tests for engine.data_loader using a tiny synthetic zip (no real data)."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from engine.data_loader import CORE_COLS, parse_name, run

HEADER = ["timestamp", "settlement_date", *CORE_COLS, "close_mid_fp"]


def _row(ts: str, bid: float, ask: float) -> list:
    mid = (bid + ask) / 2
    return [ts, "2026-07-03", *([bid, ask, mid] * 4), 0.0]


def _csv(rows: list[list]) -> bytes:
    return pd.DataFrame(rows, columns=HEADER).to_csv(index=False).encode()


def _make_zip(path: Path) -> None:
    """Outer zip -> one inner zip (USDJPY spot) with 2 days, plus junk."""
    day1 = _csv([
        _row("2026-07-01 00:02:00+00:00", 150.0, 150.02),
        _row("2026-07-01 00:01:00+00:00", 149.9, 149.92),  # out of order
        _row("2026-07-01 00:01:00+00:00", 149.9, 149.92),  # duplicate
        _row("2026-07-01 00:03:00+00:00", 150.1, 150.0),   # ask < bid
    ])
    day2 = _csv([_row("2026-07-02 00:01:00+00:00", 151.0, 151.02)])
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("USDJPY_USD_SPOT_SPOT_1000000_20260701.csv", day1)
        z.writestr("USDJPY_USD_SPOT_SPOT_1000000_20260702.csv", day2)
        z.writestr("__MACOSX/._USDJPY_USD_SPOT_SPOT_1000000_20260701.csv", b"junk")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("USDJPY_USD_SPOT_SPOT_1000000_20260701-20260831.zip", inner.getvalue())
        z.writestr("__MACOSX/._x.zip", b"junk")


def test_parse_name_spot_and_forward() -> None:
    s = parse_name("USDJPY_USD_SPOT_SPOT_1000000_20260825.csv")
    assert (s.pair, s.tenor, s.day) == ("USDJPY", "SPOT", date(2026, 8, 25))
    f = parse_name("some/dir/EURUSD_EUR_FORWARD_1M_1000000_20260701.csv")
    assert (f.pair, f.tenor, f.day) == ("EURUSD", "1M", date(2026, 7, 1))


def test_parse_name_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_name("README.txt")


def test_run_cleans_and_writes_parquet(tmp_path: Path) -> None:
    zip_path = tmp_path / "c.zip"
    _make_zip(zip_path)
    report = run(zip_path, tmp_path / "out")

    r = report.iloc[0]
    assert r["instrument"] == "USDJPY_SPOT"
    assert (r["files"], r["raw_rows"], r["dup_rows"], r["bad_rows"]) == (2, 5, 1, 1)
    assert r["kept_rows"] == 3

    df = pd.read_parquet(tmp_path / "out" / "USDJPY_SPOT.parquet")
    assert len(df) == 3
    assert df["timestamp"].is_monotonic_increasing
    assert str(df["timestamp"].dt.tz) == "UTC"
    assert set(df["pair"]) == {"USDJPY"} and set(df["tenor"]) == {"SPOT"}
    assert "close_mid_fp" in df.columns  # extra columns are kept
    assert (tmp_path / "out" / "quality_report.csv").exists()
