import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.replay_control import ReplayController, parse_start

T0 = datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc)


def ts(minute):
    return (T0 + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")


def minute_source(n, step_min=1):
    """Factory: n events `step_min` market-minutes apart, honouring start."""
    starts = []

    async def factory(start):
        starts.append(start)
        for i in range(n):
            t = ts(i * step_min)
            if start is None or t[:16] >= start:
                yield {"ts": t, "mid": 150.0 + i}

    factory.starts = starts
    return factory


class Recorder:
    def __init__(self):
        self.events, self.walls, self.states = [], [], []

    async def on_event(self, arrived_ns, event):
        self.events.append(event)
        self.walls.append(time.monotonic())

    async def on_state(self, state):
        self.states.append(state)


async def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.005)


def make(factory, rec, speed, start=None):
    return ReplayController(factory, rec.on_event, start=start, speed=speed,
                            on_state=rec.on_state)


# --------------------------------------------------------------- pacing


def test_sleeps_gap_over_speed_between_events():
    async def main():
        rec = Recorder()
        rc = make(minute_source(4), rec, speed=1200)  # 60 s gap -> 50 ms
        await rc.play()
        await wait_until(lambda: rc.status == "finished")
        return [b - a for a, b in zip(rec.walls, rec.walls[1:])]

    gaps = asyncio.run(main())
    assert len(gaps) == 3
    assert all(0.04 <= g <= 0.15 for g in gaps), gaps


def test_very_high_speed_skips_sleep():
    async def main():
        rec = Recorder()
        rc = make(minute_source(2_000), rec, speed=1_000_000)  # 60 µs gap -> no sleep
        t0 = time.monotonic()
        await rc.play()
        await wait_until(lambda: rc.status == "finished")
        return time.monotonic() - t0, len(rec.events)

    elapsed, n = asyncio.run(main())
    assert n == 2_000
    assert elapsed < 1.0


def test_long_market_gap_is_capped():
    async def main():
        rec = Recorder()
        rc = make(minute_source(2, step_min=60 * 48), rec, speed=60)  # weekend: 48 min wall
        t0 = time.monotonic()
        await rc.play()
        await wait_until(lambda: rc.status == "finished")
        return time.monotonic() - t0

    assert asyncio.run(main()) < 1.5


# --------------------------------------------------------------- controls


def test_pause_stops_events_and_start_resumes():
    async def main():
        rec = Recorder()
        rc = make(minute_source(1_000), rec, speed=6_000)  # 10 ms per event
        await rc.play()
        await wait_until(lambda: len(rec.events) >= 3)
        state = await rc.pause()
        n_paused = len(rec.events)
        await asyncio.sleep(0.15)
        frozen = len(rec.events) - n_paused
        await rc.play()
        await wait_until(lambda: len(rec.events) >= n_paused + 3)
        await rc.shutdown()
        return state, frozen

    state, frozen = asyncio.run(main())
    assert state["status"] == "paused"
    assert frozen <= 1  # at most the one already being released


def test_speed_change_interrupts_a_long_sleep():
    async def main():
        rec = Recorder()
        rc = make(minute_source(3), rec, speed=1)  # 60 s gap at 1x
        await rc.play()
        await wait_until(lambda: len(rec.events) == 1)
        t0 = time.monotonic()
        await rc.set_speed(1_000_000)
        await wait_until(lambda: rc.status == "finished")
        return time.monotonic() - t0

    assert asyncio.run(main()) < 0.5


def test_jump_restarts_source_at_new_start_and_keeps_pause_state():
    async def main():
        rec = Recorder()
        factory = minute_source(100)
        rc = make(factory, rec, speed=1_000_000, start="2026-07-30T13:00")
        await rc.pause()
        state = await rc.jump("2026-07-30T13:25")
        await asyncio.sleep(0.05)
        released_while_paused = len(rec.events)
        await rc.play()
        await wait_until(lambda: rc.status == "finished")
        return factory.starts, state, released_while_paused, rec.events[0]

    starts, state, released, first = asyncio.run(main())
    assert starts == ["2026-07-30T13:25"]
    assert state["status"] == "paused" and state["start"] == "2026-07-30T13:25"
    assert released == 0
    assert first["ts"] == "2026-07-30T13:25:00Z"


def test_start_after_finish_replays_from_start():
    async def main():
        rec = Recorder()
        factory = minute_source(3)
        rc = make(factory, rec, speed=1_000_000)
        await rc.play()
        await wait_until(lambda: rc.status == "finished")
        await rc.play()
        await wait_until(lambda: len(rec.events) == 6)
        return factory.starts

    assert len(asyncio.run(main())) == 2


def test_state_tracks_position_and_broadcasts_changes():
    async def main():
        rec = Recorder()
        rc = make(minute_source(5), rec, speed=1_000_000, start="2026-07-30")
        assert rc.state()["status"] == "idle"
        await rc.play()
        await wait_until(lambda: rc.status == "finished")
        await asyncio.sleep(0.01)  # let the "finished" notification go out
        return rc.state(), [s["status"] for s in rec.states]

    state, statuses = asyncio.run(main())
    assert state == {"status": "finished", "speed": 1_000_000.0,
                     "start": "2026-07-30T00:00", "position": "2026-07-30T13:04:00Z"}
    assert statuses[0] == "playing" and statuses[-1] == "finished"


def test_source_crash_reports_error():
    async def broken(start):
        yield {"ts": ts(0)}
        raise RuntimeError("boom")

    async def main():
        rec = Recorder()
        rc = make(broken, rec, speed=1_000_000)
        await rc.play()
        await wait_until(lambda: rc.status in {"error", "finished"})
        return rc.status

    assert asyncio.run(main()) == "error"


# --------------------------------------------------------------- validation


@pytest.mark.parametrize("raw, expected", [
    ("2026-07-30", "2026-07-30T00:00"),
    ("2026-07-30T13:25", "2026-07-30T13:25"),
    ("2026-07-30T13:25:00Z", "2026-07-30T13:25"),
    ("2026-07-30T15:25:00+02:00", "2026-07-30T13:25"),
])
def test_parse_start_normalises_to_utc(raw, expected):
    assert parse_start(raw) == expected


@pytest.mark.parametrize("bad", ["", "yesterday", "2026-13-01"])
def test_parse_start_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_start(bad)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_rejects_invalid_speed(bad):
    async def main():
        rec = Recorder()
        rc = make(minute_source(1), rec, speed=1)
        await rc.set_speed(bad)

    with pytest.raises(ValueError):
        asyncio.run(main())
