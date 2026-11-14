import asyncio
import threading
import time

import pytest

from sentinel.feed import DATA_DIR, engine_stream, mock_stream

ENGINE_KEYS = {"ts", "pair", "tenor", "mid", "spread_bps", "score", "level", "latency_us"}


async def collect(agen, n=None):
    out = []
    async for item in agen:
        out.append(item)
        if n is not None and len(out) == n:
            break
    await agen.aclose()
    return out


def fake_stream(n, delay_s=0.0, fail_at=None):
    """Blocking generator shaped like engine.stream."""
    calls = []

    def stream(pairs, tenors, speed, start, end, data_dir):
        calls.append({"pairs": pairs, "tenors": tenors, "speed": speed, "start": start})
        for i in range(n):
            if i == fail_at:
                raise RuntimeError("boom")
            time.sleep(delay_s)
            yield {"ts": f"t{i}", "pair": pairs[0], "tenor": tenors[0], "mid": 150.0 + i}

    stream.calls = calls
    return stream


# --------------------------------------------------------------- engine adapter


def test_adapter_yields_every_message_in_order():
    events = asyncio.run(collect(engine_stream(stream_fn=fake_stream(50))))
    assert [e["mid"] for e in events] == [150.0 + i for i in range(50)]


def test_adapter_runs_engine_unpaced_with_requested_start_and_subscription():
    fake = fake_stream(1)
    asyncio.run(collect(engine_stream("2026-07-30T13:25", pairs=["EURUSD"], tenors=["1W"],
                                      stream_fn=fake)))
    assert fake.calls == [{"pairs": ["EURUSD"], "tenors": ["1W"], "speed": None,
                           "start": "2026-07-30T13:25"}]


def test_adapter_does_not_block_the_event_loop():
    """While the engine thread is busy, the loop keeps serving."""

    async def main():
        heartbeats = 0

        async def heartbeat():
            nonlocal heartbeats
            while True:
                heartbeats += 1
                await asyncio.sleep(0.01)

        hb = asyncio.create_task(heartbeat())
        await collect(engine_stream(stream_fn=fake_stream(5, delay_s=0.05)))
        hb.cancel()
        return heartbeats

    assert asyncio.run(main()) >= 10


def test_adapter_surfaces_engine_errors():
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(collect(engine_stream(stream_fn=fake_stream(10, fail_at=3))))


def test_adapter_stops_worker_thread_when_consumer_leaves():
    asyncio.run(collect(engine_stream(stream_fn=fake_stream(10_000, delay_s=0.001)), n=3))
    deadline = time.monotonic() + 3.0
    while any(t.name == "engine-feed" for t in threading.enumerate()):
        assert time.monotonic() < deadline, "engine-feed thread did not stop"
        time.sleep(0.05)


@pytest.mark.skipif(not any(DATA_DIR.glob("USDJPY_SPOT.parquet")),
                    reason="run `python -m engine.data_loader` first")
def test_real_engine_30_july_turns_red_after_1335():
    pytest.importorskip("engine")
    msgs = asyncio.run(collect(engine_stream("2026-07-30T13:00", end="2026-07-30T14:00")))

    assert set(msgs[0]) == ENGINE_KEYS
    assert msgs[0]["ts"] == "2026-07-30T13:00:00Z"
    assert all(m["level"] == "green" for m in msgs if m["ts"] <= "2026-07-30T13:35:00Z")
    assert any(m["level"] == "red" for m in msgs if m["ts"] <= "2026-07-30T13:45:00Z")


# --------------------------------------------------------------- mock


def take_mock(n, start=None, **kwargs):
    return asyncio.run(collect(mock_stream(start, **kwargs), n=n))


def test_mock_emits_engine_format():
    for event in take_mock(120):
        assert set(event) == ENGINE_KEYS
        assert event["pair"] == "USDJPY" and event["tenor"] == "SPOT"
        assert event["mid"] > 0 and event["spread_bps"] > 0
        assert 0 <= event["score"] <= 100
        assert event["level"] in {"green", "yellow", "amber", "red"}


def test_mock_has_sudden_drop_that_turns_red_and_is_calm_before():
    events = take_mock(120)
    before, drop = events[:30], events[30:50]

    assert all(e["level"] == "green" for e in before[5:])
    assert drop[-1]["mid"] / before[-1]["mid"] - 1 < -0.025
    assert any(e["level"] == "red" for e in drop)
    assert drop[0]["ts"] == "2026-07-30T13:30:00Z"


def test_mock_start_skips_ahead_on_the_same_path():
    full = take_mock(40)
    jumped = take_mock(10, start="2026-07-30T13:30")
    assert jumped == full[30:40]


def test_mock_is_reproducible_by_seed():
    assert take_mock(60, seed=1) == take_mock(60, seed=1)
    assert take_mock(60, seed=1) != take_mock(60, seed=2)
