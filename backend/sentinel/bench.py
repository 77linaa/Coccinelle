"""Benchmark: how long the backend takes to handle one price.

Runs the real app on Meryem's engine stream (USD/JPY spot + 1W/1M/3M forwards)
as fast as possible, with one connected dashboard, and times every call of the
tick handler: quote book → alert check → portfolio → judge/Sentinel race →
message build → JSON → send. The margin forecast keeps running in its worker
thread meanwhile, exactly as in production, so its CPU contention is included.

Reports median, p99 (slowest 1%) and max, for spot prices (the ones that
produce dashboard ticks and alerts) and for all prices including forwards:
- backend tick handling  (this module's measurement)
- engine processing      (Meryem's ``latency_us`` on the same prices)
- total price-to-alert   (engine + backend)

Run from the repo root:

    PYTHONPATH=backend backend/.venv/bin/python -m sentinel.bench
    PYTHONPATH=backend backend/.venv/bin/python -m sentinel.bench --start 2026-07-01 --end 2026-08-31
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import time
from typing import Any

import numpy as np


class _NullSocket:
    """A dashboard that accepts every message instantly (no network in the timing)."""

    async def send_text(self, data: str) -> None:
        self.bytes = len(data)


def _stats(values_ms: np.ndarray) -> dict[str, float]:
    return {
        "median_ms": float(np.median(values_ms)),
        "p99_ms": float(np.percentile(values_ms, 99)),
        "max_ms": float(values_ms.max()),
    }


async def run(start: str, end: str | None, forecast: bool) -> dict[str, Any]:
    os.environ.setdefault("SENTINEL_AI", "off")  # never bill API calls from a benchmark
    from functools import partial

    from sentinel.api import create_app
    from sentinel.feed import engine_stream
    from sentinel.hedges import HEDGE_TENORS

    source = partial(engine_stream, tenors=("SPOT", *HEDGE_TENORS), end=end)
    app = create_app(source=source, source_name="engine", speed=1_000_000.0, start=start,
                     autoplay=False, forecaster="auto" if forecast else None,
                     forecast_interval_s=0.5)
    replay = app.state.replay
    app.state.manager.add(_NullSocket())

    backend_ns: list[int] = []
    engine_us: list[float] = []
    is_spot: list[bool] = []
    handler = replay._on_event

    async def timed(arrived_ns: int, event: dict[str, Any]) -> None:
        t0 = time.perf_counter_ns()
        await handler(arrived_ns, event)
        backend_ns.append(time.perf_counter_ns() - t0)
        engine_us.append(event["latency_us"])
        is_spot.append(event["tenor"] == "SPOT")

    replay._on_event = timed

    async with app.router.lifespan_context(app):
        wall0 = time.perf_counter()
        await replay.play()
        while replay.status == "playing":
            await asyncio.sleep(0.05)
        wall = time.perf_counter() - wall0

    backend = np.array(backend_ns) / 1e6
    engine = np.array(engine_us) / 1e3
    spot = np.array(is_spot)
    return {
        "window": {"start": start, "end": end},
        "prices": len(backend), "spot_prices": int(spot.sum()), "wall_s": wall,
        "throughput_per_s": len(backend) / wall,
        "spot": {"backend": _stats(backend[spot]), "engine": _stats(engine[spot]),
                 "total": _stats(backend[spot] + engine[spot])},
        "all": {"backend": _stats(backend), "engine": _stats(engine),
                "total": _stats(backend + engine)},
        "forecast_running": forecast,
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
    }


def to_markdown(r: dict[str, Any]) -> str:
    sp, al = r["spot"], r["all"]
    rows = [("Engine: risk signal (spot)", sp["engine"]),
            ("Backend: tick handling (spot)", sp["backend"]),
            ("**Total price → alert (spot)**", sp["total"]),
            ("Total, all prices incl. forwards", al["total"])]
    lines = [
        f"Replay {r['window']['start']} → {r['window']['end'] or 'end of data'}: "
        f"{r['prices']:,} prices ({r['spot_prices']:,} spot + forwards), "
        f"{r['throughput_per_s']:,.0f} prices/s, margin forecast running: "
        f"{'yes' if r['forecast_running'] else 'no'}. {r['machine']}.",
        "",
        "| Stage | Median | Slowest 1% (p99) | Max |",
        "|---|---|---|---|",
    ]
    for name, s in rows:
        lines.append(f"| {name} | {s['median_ms']:.3f} ms | {s['p99_ms']:.3f} ms | "
                     f"{s['max_ms']:.2f} ms |")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", default="2026-07-30T00:00")
    p.add_argument("--end", default="2026-07-31T23:59")
    p.add_argument("--no-forecast", action="store_true", help="exclude forecast contention")
    p.add_argument("--json", help="also write the raw results here")
    a = p.parse_args()
    result = asyncio.run(run(a.start, a.end, forecast=not a.no_forecast))
    print(to_markdown(result))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
