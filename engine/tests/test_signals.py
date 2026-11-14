"""Unit tests for the streaming building blocks in engine.signals."""

from __future__ import annotations

import math
import random

import pandas as pd
import pytest

from engine.signals import (Ewma, RollingWindow, SignalEngine, combine,
                            level_for, ramp)


def test_ramp_and_levels() -> None:
    assert ramp(0, 1, 3) == 0 and ramp(2, 1, 3) == 0.5 and ramp(9, 1, 3) == 1
    assert [level_for(s) for s in (0, 24.9, 25, 50, 74.9, 75, 100)] == [
        "green", "green", "yellow", "amber", "amber", "red", "red"]
    assert combine(1, 0, 0, 0, 1) == 0  # calm market -> 0
    assert combine(99, 99, 99, 99, 99) == pytest.approx(100)  # all extreme


def test_ewma_matches_pandas_adjust_true() -> None:
    xs = [random.Random(1).gauss(0, 1) for _ in range(500)]
    lam = 0.5 ** (1 / 15)
    e, out = Ewma(lam), []
    for x in xs:
        e.update(x)
        out.append(e.value)
    ref = pd.Series(xs).ewm(halflife=15, adjust=True).mean()
    assert out == pytest.approx(ref.tolist(), rel=1e-10)


def test_rolling_window_matches_brute_force() -> None:
    rng = random.Random(7)
    span = 10
    w = RollingWindow(span)
    hist: list[tuple[int, float]] = []
    t = 0
    for _ in range(2000):
        t += rng.choice([1, 1, 2, 5, 30])  # irregular gaps, some > span
        x = 100 + rng.gauss(0, 1)
        w.push(t, x)
        hist.append((t, x))
        inside = [v for (ti, v) in hist if ti > t - span]
        m = sum(inside) / len(inside)
        sd = math.sqrt(sum((v - m) ** 2 for v in inside) / len(inside))
        got_m, got_sd = w.mean_std()
        assert (w.high, w.low) == (max(inside), min(inside))
        assert got_m == pytest.approx(m, abs=1e-9)
        assert got_sd == pytest.approx(sd, abs=1e-7)


def test_instruments_are_independent() -> None:
    eng = SignalEngine()
    a1 = eng.update("A", "SPOT", 0, 1.0, 1.1, 1.05)
    eng.update("B", "SPOT", 1, 5.0, 5.5, 5.25)
    a2 = eng.update("A", "SPOT", 2, 1.0, 1.1, 1.05)
    assert a1.ret == 0 and a2.ret == 0  # B's price never leaks into A
    assert len(eng.states) == 2
