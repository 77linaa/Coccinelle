"""Replay clock: play / pause / speed / jump over any event source.

A *source factory* takes a start time and returns an async iterator of raw,
unpaced messages (``engine_stream`` or ``mock_stream``). The controller paces
them in market time: between consecutive events it sleeps

    gap_between_events / speed

so ``speed=60`` plays one market minute per wall-clock second. Pause and speed
changes interrupt a sleep immediately rather than waiting for it to finish.
At very high speeds (wait under ``MIN_SLEEP_S``) the sleep is skipped and the
loop only yields control, so the server stays responsive.

Each event is timestamped (``perf_counter_ns``) at the instant the replay clock
releases it — the moment a live feed would deliver it — so downstream latency
measures processing, not the deliberate pacing.

All state lives in this one object and is only touched from the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("sentinel.replay")

__all__ = ["ReplayController", "SourceFactory", "parse_start"]

SourceFactory = Callable[[str | None], AsyncIterator[dict[str, Any]]]
EventHandler = Callable[[int, dict[str, Any]], Awaitable[None]]
StateHandler = Callable[[dict[str, Any]], Awaitable[None]]

MIN_SLEEP_S = 0.001     # below this, skip the sleep and just yield to the loop
MAX_GAP_S = 600.0       # market gaps longer than this (closed, missing data)...
MAX_IDLE_S = 1.0        # ...wait at most this long in wall-clock time


def parse_start(value: str) -> str:
    """Normalise ``2026-07-30`` / ``2026-07-30T13:25`` / ``...Z`` to UTC ``YYYY-MM-DDTHH:MM``."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _ts_seconds(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class ReplayController:
    def __init__(
        self,
        source_factory: SourceFactory,
        on_event: EventHandler,
        *,
        start: str | None,
        speed: float = 60.0,
        on_state: StateHandler | None = None,
    ) -> None:
        self._factory = source_factory
        self._on_event = on_event
        self._on_state = on_state
        self.start = parse_start(start) if start else None
        self.speed = self._check_speed(speed)
        self.position: str | None = None  # ts of the last event released

        self._playing = asyncio.Event()
        self._wake = asyncio.Event()      # interrupts a pacing sleep
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ controls

    async def play(self) -> dict[str, Any]:
        """Start, resume, or restart from ``start`` if the replay finished."""
        async with self._lock:
            if self._task is None or self._task.done():
                self.position = None
                self._spawn()
            self._playing.set()
            self._wake.set()
        return await self._changed()

    async def pause(self) -> dict[str, Any]:
        self._playing.clear()
        self._wake.set()
        return await self._changed()

    async def set_speed(self, speed: float) -> dict[str, Any]:
        self.speed = self._check_speed(speed)
        self._wake.set()
        return await self._changed()

    async def jump(self, start: str) -> dict[str, Any]:
        """Restart the source at ``start``, keeping the current play/pause state."""
        start = parse_start(start)
        async with self._lock:
            await self._cancel()
            self.start, self.position = start, None
            self._spawn()
            self._wake.set()
        return await self._changed()

    async def shutdown(self) -> None:
        async with self._lock:
            await self._cancel()

    # ------------------------------------------------------------ state

    @property
    def status(self) -> str:
        if self._task is None:
            return "idle"
        if self._task.done():
            if not self._task.cancelled() and self._task.exception() is not None:
                return "error"
            return "finished"
        return "playing" if self._playing.is_set() else "paused"

    def state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "speed": self.speed,
            "start": self.start,
            "position": self.position,
        }

    # ------------------------------------------------------------ internals

    @staticmethod
    def _check_speed(speed: float) -> float:
        speed = float(speed)
        if not speed > 0 or speed == float("inf"):
            raise ValueError(f"speed must be a positive finite number, got {speed!r}")
        return speed

    async def _changed(self) -> dict[str, Any]:
        state = self.state()
        if self._on_state is not None:
            await self._on_state(state)
        return state

    def _spawn(self) -> None:
        self._task = asyncio.create_task(self._run(), name="replay")
        self._task.add_done_callback(self._on_done)

    async def _cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _on_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        if task.exception() is not None:
            logger.error("Replay source crashed", exc_info=task.exception())
        if self._on_state is not None:
            # Tell dashboards the replay ended (finished or error).
            asyncio.get_running_loop().create_task(self._changed())

    async def _run(self) -> None:
        source = self._factory(self.start)
        prev_s: float | None = None
        try:
            async for event in source:
                try:
                    now_s = _ts_seconds(event["ts"])
                except (KeyError, TypeError, ValueError):
                    now_s = None  # unpaceable; let the handler reject it
                if prev_s is not None and now_s is not None and now_s > prev_s:
                    await self._pace(now_s - prev_s)
                await self._playing.wait()
                if now_s is not None:
                    prev_s = now_s
                    self.position = event["ts"]
                await self._on_event(time.perf_counter_ns(), event)
        finally:
            with contextlib.suppress(Exception):
                await source.aclose()

    async def _pace(self, gap_s: float) -> None:
        """Sleep gap_s / speed of wall time; pause and speed changes apply mid-sleep."""
        speed = self.speed
        wall = gap_s / speed
        if gap_s > MAX_GAP_S:  # market closed / missing data: don't sit idle
            wall = min(wall, MAX_IDLE_S)
        loop = asyncio.get_running_loop()
        while True:
            await self._playing.wait()
            if self.speed != speed:  # rescale what's left to the new speed
                wall *= speed / self.speed
                speed = self.speed
            if wall < MIN_SLEEP_S:
                await asyncio.sleep(0)
                return
            self._wake.clear()
            t0 = loop.time()
            try:
                await asyncio.wait_for(self._wake.wait(), wall)
            except asyncio.TimeoutError:
                return
            wall -= loop.time() - t0
