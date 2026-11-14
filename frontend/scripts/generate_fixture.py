"""Extract authentic FX spot replay fixtures from the SGX challenge ZIP.

The fixtures contain ONLY prices. Mock portfolio, storm scores, forecasts and timing
are illustrative frontend data until the team's FastAPI backend is connected.

Usage: python scripts/generate_fixture.py --archive /path/to/Challenge1.zip
"""

import argparse
import csv
import io
import json
import zipfile
from pathlib import Path


PAIRS = ("USDJPY", "EURUSD", "USDSGD")


def generate(archive: Path, output_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with zipfile.ZipFile(archive) as outer:
        for pair in PAIRS:
            nested_name = next(
                name for name in outer.namelist()
                if name.startswith(f"{pair}_") and "_SPOT_SPOT" in name
                and name.endswith(".zip")
            )
            with zipfile.ZipFile(io.BytesIO(outer.read(nested_name))) as inner:
                csv_name = next(
                    name for name in inner.namelist() if name.endswith("20260730.csv")
                )
                with inner.open(csv_name) as handle:
                    reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
                    rows = []
                    for row in reader:
                        minute = row["timestamp"][11:16]
                        if not ("12:00" <= minute <= "15:00"):
                            continue
                        if int(minute[-2:]) % 2 != 0:
                            continue
                        rows.append({
                            "timestamp": row["timestamp"].replace(" ", "T").replace("+00:00", "Z"),
                            "mid": float(row["close_mid"]),
                            "bid": float(row["close_bid"]),
                            "ask": float(row["close_ask"]),
                        })
            if not rows:
                raise ValueError(f"No matching 30 July spot quotes for {pair}")
            output_dir.mkdir(parents=True, exist_ok=True)
            output = output_dir / f"{pair.lower()}-2026-07-30.json"
            output.write_text(json.dumps(rows, separators=(",", ":")) + "\n", encoding="utf-8")
            counts[pair] = len(rows)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "src/mock",
    )
    args = parser.parse_args()
    print(f"Wrote authentic spot quote rows: {generate(args.archive, args.output_dir)}")
