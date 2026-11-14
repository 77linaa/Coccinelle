"""Tide table: the market's known rough hours, derived from the data.

Like a tide table for sailors, this is a historical, offline summary of when
the FX market tends to be rough. It is NOT an input to the live score (that
would use future data); it is context for the dashboard and the pitch.

For each pair (spot) and hour of day (UTC) we report:
- vol_index:    mean |1-min return| in that hour / the pair's overall mean
- spread_index: mean spread in that hour / the pair's overall mean
- alert_pct:    share of minutes where the live engine was amber or red
- rough:        vol_index >= ROUGH (1.5x a normal hour)
plus the hottest individual minutes of the day and the largest 1-min jumps.

Run:  python -m engine.tide_table
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from engine.replay import DEFAULT_DATA, load_frame
from engine.risk_stream import stream

ROUGH = 1.5
ONE_MIN = pd.Timedelta(minutes=1)


def minute_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Per-pair 1-minute log returns and spreads; returns across gaps
    (overnight, weekend, missing minutes) are dropped, not counted as jumps."""
    out = []
    for pair, g in df.groupby("pair", sort=True):
        g = g.sort_values("timestamp")
        r = np.log(g["close_mid"]).diff()
        ok = g["timestamp"].diff() == ONE_MIN
        out.append(pd.DataFrame({
            "pair": pair,
            "timestamp": g["timestamp"],
            "abs_ret_bp": (r.abs() * 1e4).where(ok),
            "spread_bp": (g["close_ask"] - g["close_bid"]) / g["close_mid"] * 1e4,
        }))
    m = pd.concat(out, ignore_index=True)
    m["hour"] = m["timestamp"].dt.hour
    m["hhmm"] = m["timestamp"].dt.strftime("%H:%M")
    return m


def alert_share(data_dir: Path, pairs: list[str]) -> pd.DataFrame:
    """Share of minutes per (pair, hour) the live engine flagged amber/red."""
    rows = [(m["pair"], int(m["ts"][11:13]), m["level"] in ("amber", "red"))
            for m in stream(pairs, ["SPOT"], data_dir=data_dir)]
    a = pd.DataFrame(rows, columns=["pair", "hour", "alert"])
    return (a.groupby(["pair", "hour"])["alert"].mean() * 100).rename("alert_pct")


def tide_table(data_dir: Path = DEFAULT_DATA) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (hourly table, hottest minutes, largest jumps) for spot."""
    df = load_frame(data_dir, tenors=["SPOT"])
    m = minute_returns(df)
    pairs = sorted(m["pair"].unique())

    hourly = m.groupby(["pair", "hour"]).agg(
        vol=("abs_ret_bp", "mean"), spread=("spread_bp", "mean"))
    base = m.groupby("pair").agg(vol=("abs_ret_bp", "mean"),
                                 spread=("spread_bp", "mean"))
    hourly["vol_index"] = hourly["vol"] / base["vol"].reindex(hourly.index, level=0).values
    hourly["spread_index"] = (hourly["spread"]
                              / base["spread"].reindex(hourly.index, level=0).values)
    hourly = hourly.join(alert_share(data_dir, pairs))
    hourly["rough"] = hourly["vol_index"] >= ROUGH
    hourly = hourly.reset_index()

    hot = (m.groupby(["pair", "hhmm"])["abs_ret_bp"].mean()
           .rename("mean_abs_ret_bp").reset_index()
           .sort_values("mean_abs_ret_bp", ascending=False)
           .groupby("pair").head(5))
    jumps = (m.dropna(subset=["abs_ret_bp"])
             .sort_values("abs_ret_bp", ascending=False)
             .groupby("pair").head(5)[["pair", "timestamp", "abs_ret_bp"]])
    return hourly, hot, jumps


def main() -> None:
    hourly, hot, jumps = tide_table()
    pd.set_option("display.width", 120)
    for pair, g in hourly.groupby("pair"):
        print(f"\n=== {pair} spot: hours UTC (vol/spread index: 1.0 = typical) ===")
        g = g.assign(bar=(g["vol_index"] * 10).round().astype(int).map(lambda n: "#" * n))
        print(g[["hour", "vol_index", "spread_index", "alert_pct", "rough", "bar"]]
              .to_string(index=False, float_format=lambda x: f"{x:.2f}"))
        print("rough hours:", ", ".join(f"{h:02d}:00" for h in g.loc[g["rough"], "hour"]) or "none")
    print("\n=== Hottest minutes of the day (mean |1-min return|, bp) ===")
    print(hot.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("\n=== Largest single 1-minute jumps (bp) ===")
    print(jumps.to_string(index=False, float_format=lambda x: f"{x:.1f}"))
    out = DEFAULT_DATA / "tide_table.csv"
    hourly.to_csv(out, index=False)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
