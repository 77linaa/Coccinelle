"""Event sources: Meryem's real engine stream, or a mock stand-in.

Both are *source factories* with the same interface — ``factory(start)``
returns an async iterator of raw, unpaced messages in the engine's format:

    {"ts", "pair", "tenor", "mid", "spread_bps", "score", "level", "latency_us"}

Pacing (play / pause / speed / jump) is done by ``ReplayController``, so the
sources run as fast as the consumer pulls.

``engine_stream`` adapts ``engine.stream`` — a *blocking* generator — by running
it on a worker thread and handing messages to the event loop through a bounded
queue. The queue applies back-pressure, so the engine never runs far ahead of
the replay clock and the server never blocks on pandas or file I/O.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import random
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = ["DATA_DIR", "engine_stream", "mock_stream"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "processed"

_DONE = object()
_PUT_POLL_S = 0.5


# ============================================================ real engine


async def engine_stream(
    start: str | None = None,
    *,
    pairs: Iterable[str] = ("USDJPY",),
    tenors: Iterable[str] = ("SPOT",),
    end: str | None = None,
    data_dir: Path = DATA_DIR,
    queue_size: int = 256,
    stream_fn: Callable[..., Iterator[dict[str, Any]]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run ``engine.stream`` (unpaced) on a worker thread; yield its messages here.

    Quotes before ``start`` still warm up the engine's indicators. ``stream_fn``
    exists for tests; by default the real ``engine.stream`` is imported lazily,
    so the mock mode works without pandas installed.
    """
    if stream_fn is None:
        from engine import stream as stream_fn

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_size)
    stop = threading.Event()

    def put(item: Any) -> bool:
        """Blocking put from the worker; gives up if the consumer has gone."""
        fut = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
        while True:
            try:
                fut.result(timeout=_PUT_POLL_S)
                return True
            except concurrent.futures.TimeoutError:
                if stop.is_set():
                    fut.cancel()
                    return False

    def worker() -> None:
        final: Any = _DONE
        try:
            for msg in stream_fn(list(pairs), list(tenors), None, start, end, data_dir):
                if stop.is_set() or not put(msg):
                    return
        except Exception as exc:  # surfaced to the consumer below
            final = exc
        if not stop.is_set():
            put(final)

    thread = threading.Thread(target=worker, name="engine-feed", daemon=True)
    thread.start()
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stop.set()


# ============================================================ mock


MOCK_START = datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc)
MOCK_START_MID = 155.00

_CYCLE_TICKS = 120        # one simulated 2-hour cycle at 1-minute bars
_DROP_START = 30          # drop begins at 13:30 UTC in the first cycle
_DROP_TICKS = 20          # ...and lasts 20 minutes
_DROP_TOTAL = -0.03       # ~3% fall over the drop window
_CALM_VOL = 0.0002        # 0.02% per-minute noise in calm periods
_MEAN_REVERSION = 0.03    # pull back toward the start level after the drop
_BASE_SPREAD_BPS = 0.5
_SCORE_EWMA = 0.3


def _mock_level(score: float) -> str:
    if score >= 75:
        return "red"
    if score >= 50:
        return "amber"
    if score >= 25:
        return "yellow"
    return "green"


async def mock_stream(start: str | None = None, *, seed: int = 7) -> AsyncIterator[dict[str, Any]]:
    """Infinite fake USD/JPY spot feed (1-minute bars) in the engine's format.

    Calm random walk → sudden ~3% drop with widening spreads → recovery, repeated.
    Ticks before ``start`` are generated (so the walk is identical) but not
    yielded. The score/level are placeholders shaped like the Storm Risk Score
    so the dashboard can be built without the data; they are NOT the real model.
    """
    rng = random.Random(seed)
    drop_step = math.log1p(_DROP_TOTAL) / _DROP_TICKS
    start_at = (
        datetime.fromisoformat(start).replace(tzinfo=timezone.utc) if start else MOCK_START
    )
    mid = MOCK_START_MID
    stress = 0.0
    tick = 0

    while True:
        phase = tick % _CYCLE_TICKS
        in_drop = _DROP_START <= phase < _DROP_START + _DROP_TICKS

        log_ret = rng.gauss(0.0, _CALM_VOL)
        if in_drop:
            log_ret += drop_step
        else:
            log_ret += _MEAN_REVERSION * math.log(MOCK_START_MID / mid)
        mid *= math.exp(log_ret)

        z = abs(log_ret) / _CALM_VOL
        stress = _SCORE_EWMA * z + (1.0 - _SCORE_EWMA) * stress
        score = max(0.0, min(100.0, (stress - 1.0) * 20.0))

        at = MOCK_START + timedelta(minutes=tick)
        tick += 1
        if at < start_at:
            continue
        yield {
            "ts": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pair": "USDJPY",
            "tenor": "SPOT",
            "mid": round(mid, 3),
            "spread_bps": round(_BASE_SPREAD_BPS * (1.0 + 0.5 * stress), 3),
            "score": round(score, 1),
            "level": _mock_level(score),
            "latency_us": 0.0,  # the real engine reports its own processing time
        }
