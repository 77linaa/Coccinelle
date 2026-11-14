"""Extract SGX FX data, clean it, write one Parquet file per instrument.

The challenge zip contains 30 inner zips (3 pairs x 10 tenors), each holding
one CSV per trading day. We read everything in memory (no extraction to
disk), clean each instrument and write `data/processed/<PAIR>_<TENOR>.parquet`,
then print a data-quality report.

Run:  python -m engine.data_loader [--zip PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterator

import pandas as pd

# Absolute, so it works whatever directory the caller runs from.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = REPO_ROOT / "data" / "raw" / "Challenge1.zip"
DEFAULT_OUT = REPO_ROOT / "data" / "processed"

# e.g. USDJPY_USD_SPOT_SPOT_1000000_20260825.csv
#      EURUSD_EUR_FORWARD_1M_1000000_20260701.csv
NAME_RE = re.compile(
    r"^(?P<pair>[A-Z]{6})_[A-Z]{3}_(?:SPOT|FORWARD)_(?P<tenor>[A-Z0-9]+)"
    r"_\d+_(?P<day>\d{8})\.csv$"
)

# Core price columns: these must be present and sane for a row to be kept.
# (The *_fp forward-point columns can legitimately be negative or zero.)
BARS = ("open", "high", "low", "close")
CORE_COLS = [f"{b}_{s}" for b in BARS for s in ("bid", "ask", "mid")]


@dataclass
class FileInfo:
    """Metadata parsed from a CSV file name."""

    pair: str
    tenor: str
    day: date


@dataclass
class InstrumentStats:
    """Cleaning statistics for one instrument (pair + tenor)."""

    pair: str
    tenor: str
    files: int = 0
    raw_rows: int = 0
    dup_rows: int = 0  # repeated timestamps
    bad_rows: int = 0  # NaN, non-positive price, or ask < bid
    days: set[date] = field(default_factory=set)

    @property
    def kept_rows(self) -> int:
        return self.raw_rows - self.dup_rows - self.bad_rows


def parse_name(name: str) -> FileInfo:
    """Parse pair, tenor and day from a CSV file name (path prefix ignored)."""
    m = NAME_RE.match(Path(name).name)
    if not m:
        raise ValueError(f"Unexpected file name: {name}")
    d = m["day"]
    return FileInfo(m["pair"], m["tenor"], date(int(d[:4]), int(d[4:6]), int(d[6:])))


def _is_junk(name: str) -> bool:
    """macOS metadata entries (__MACOSX/, ._foo) are not real data."""
    return "__MACOSX" in name or Path(name).name.startswith("._")


def iter_inner_zips(zip_path: Path) -> Iterator[tuple[str, zipfile.ZipFile]]:
    """Yield (name, opened ZipFile) for each inner zip, read in memory."""
    with zipfile.ZipFile(zip_path) as outer:
        for name in sorted(outer.namelist()):
            if name.endswith(".zip") and not _is_junk(name):
                yield name, zipfile.ZipFile(io.BytesIO(outer.read(name)))


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    """Parse one daily CSV: UTC timestamps, float prices."""
    df = pd.read_csv(io.BytesIO(raw))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
    return df


def clean(df: pd.DataFrame, stats: InstrumentStats) -> pd.DataFrame:
    """Sort, drop repeated timestamps and bad quotes; update `stats` counts."""
    df = df.sort_values("timestamp", kind="stable")
    dup = df["timestamp"].duplicated(keep="last")
    stats.dup_rows += int(dup.sum())
    df = df[~dup]

    core = df[CORE_COLS]
    bad = core.isna().any(axis=1) | (core <= 0).any(axis=1)
    for b in BARS:
        bad |= df[f"{b}_ask"] < df[f"{b}_bid"]
    stats.bad_rows += int(bad.sum())
    return df[~bad].reset_index(drop=True)


def load_instrument(inner: zipfile.ZipFile) -> tuple[pd.DataFrame, InstrumentStats]:
    """Read all daily CSVs of one inner zip into a single clean DataFrame."""
    frames: list[pd.DataFrame] = []
    stats: InstrumentStats | None = None
    for name in sorted(inner.namelist()):
        if not name.endswith(".csv") or _is_junk(name):
            continue
        info = parse_name(name)
        if stats is None:
            stats = InstrumentStats(info.pair, info.tenor)
        df = read_csv_bytes(inner.read(name))
        stats.files += 1
        stats.raw_rows += len(df)
        stats.days.add(info.day)
        frames.append(df)
    if stats is None:
        raise ValueError("Inner zip contains no CSV files")

    df = clean(pd.concat(frames, ignore_index=True), stats)
    df.insert(1, "pair", stats.pair)
    df.insert(2, "tenor", stats.tenor)
    return df, stats


def quality_report(all_stats: list[InstrumentStats]) -> pd.DataFrame:
    """One row per instrument. 'Missing days' are judged against the union of
    days seen in any file, i.e. the days the market was actually open."""
    trading_days = sorted(set().union(*(s.days for s in all_stats)))
    rows = []
    for s in all_stats:
        missing = [d.isoformat() for d in trading_days if d not in s.days]
        rows.append({
            "instrument": f"{s.pair}_{s.tenor}",
            "files": s.files,
            "raw_rows": s.raw_rows,
            "dup_rows": s.dup_rows,
            "bad_rows": s.bad_rows,
            "kept_rows": s.kept_rows,
            "first_day": min(s.days).isoformat(),
            "last_day": max(s.days).isoformat(),
            "missing_days": len(missing),
            "missing_list": " ".join(missing),
        })
    return pd.DataFrame(rows)


def run(zip_path: Path = DEFAULT_ZIP, out_dir: Path = DEFAULT_OUT) -> pd.DataFrame:
    """Full pipeline: zip -> clean Parquet per instrument -> quality report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    all_stats: list[InstrumentStats] = []
    first_ts, last_ts = None, None
    for name, inner in iter_inner_zips(zip_path):
        df, stats = load_instrument(inner)
        df.to_parquet(out_dir / f"{stats.pair}_{stats.tenor}.parquet", index=False)
        all_stats.append(stats)
        lo, hi = df["timestamp"].min(), df["timestamp"].max()
        first_ts = lo if first_ts is None else min(first_ts, lo)
        last_ts = hi if last_ts is None else max(last_ts, hi)
        print(f"  {stats.pair}_{stats.tenor:<5} {stats.kept_rows:>8,} rows")

    report = quality_report(all_stats)
    report.to_csv(out_dir / "quality_report.csv", index=False)
    _print_report(report, first_ts, last_ts)
    return report


def _print_report(report: pd.DataFrame, first_ts, last_ts) -> None:
    """Human-readable summary of the quality report."""
    cols = ["instrument", "files", "raw_rows", "dup_rows", "bad_rows",
            "kept_rows", "missing_days"]
    print("\n=== Data-quality report ===")
    print(report[cols].to_string(index=False))
    print(f"\nInstruments : {len(report)}")
    print(f"CSV files   : {report['files'].sum():,}")
    print(f"Raw rows    : {report['raw_rows'].sum():,}")
    print(f"Kept rows   : {report['kept_rows'].sum():,}")
    print(f"Date range  : {first_ts} -> {last_ts}")
    gaps = report[report["missing_days"] > 0]
    if not gaps.empty:
        print("\nMissing days:")
        for _, r in gaps.iterrows():
            print(f"  {r['instrument']:<12} {r['missing_list']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    run(args.zip, args.out)


if __name__ == "__main__":
    main()
