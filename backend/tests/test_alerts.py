import pytest

from sentinel.alerts import AlertTracker


def ev(level, minute=0, pair="USDJPY", tenor="SPOT", score=None, mid=162.0):
    score = {"green": 5.0, "yellow": 30.0, "amber": 60.0, "red": 90.0}[level] if score is None else score
    return {"ts": f"2026-07-30T13:{minute:02d}:00Z", "pair": pair, "tenor": tenor,
            "mid": mid, "score": score, "level": level}


def levels(tracker, seq):
    out = []
    for i, level in enumerate(seq):
        alert = tracker.update(ev(level, i))
        out.append(None if alert is None else (alert.prev_level, alert.level, alert.escalation))
    return out


def test_alerts_only_on_level_change():
    seq = ["green", "green", "yellow", "amber", "red", "red", "red", "amber", "green"]
    assert levels(AlertTracker(), seq) == [
        None,
        None,
        ("green", "yellow", True),
        ("yellow", "amber", True),
        ("amber", "red", True),
        None,
        None,
        ("red", "amber", False),
        ("amber", "green", False),
    ]


def test_jump_straight_from_green_to_red_is_one_alert():
    assert levels(AlertTracker(), ["green", "red"])[1] == ("green", "red", True)


def test_first_message_is_silent_if_green_but_alerts_if_already_stormy():
    assert AlertTracker().update(ev("green")) is None
    alert = AlertTracker().update(ev("red"))
    assert (alert.prev_level, alert.level, alert.escalation) == (None, "red", True)


def test_instruments_are_tracked_independently():
    t = AlertTracker()
    t.update(ev("green", pair="USDJPY"))
    t.update(ev("green", pair="EURUSD"))
    assert t.update(ev("red", pair="USDJPY")) is not None
    assert t.update(ev("green", pair="EURUSD")) is None
    assert t.update(ev("red", pair="USDJPY", tenor="1W")) is not None


def test_reset_forgets_levels():
    t = AlertTracker()
    t.update(ev("red"))
    t.reset()
    assert t.update(ev("green")) is None


def test_alert_carries_context_and_serialises():
    t = AlertTracker()
    t.update(ev("green"))
    alert = t.update(ev("red", minute=38, score=88.2, mid=161.417))
    assert alert.to_dict() == {
        "ts": "2026-07-30T13:38:00Z", "pair": "USDJPY", "tenor": "SPOT",
        "prev_level": "green", "level": "red", "score": 88.2, "mid": 161.417,
        "escalation": True,
    }


def test_unknown_level_rejected():
    with pytest.raises(ValueError):
        AlertTracker().update(ev("green") | {"level": "purple"})
