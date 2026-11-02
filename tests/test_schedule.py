"""Tests for the poll schedule (no Home Assistant)."""

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import random
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "schedule", Path(__file__).resolve().parent.parent / "custom_components" / "yclass" / "schedule.py"
)
schedule = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(schedule)

SWEDEN = ZoneInfo("Europe/Stockholm")


def _next(hour: int, minute: int = 0, day: int = 3) -> list[datetime]:
    now = datetime(2026, 11, day, hour, minute, tzinfo=SWEDEN)
    rng = random.Random(1)
    return [schedule.next_poll(now, rng).astimezone(SWEDEN) for _ in range(200)]


def test_daytime_gap_is_random() -> None:
    now = datetime(2026, 11, 3, 10, 0, tzinfo=SWEDEN)
    polls = _next(10)
    assert all(timedelta(minutes=70) <= p - now <= timedelta(minutes=110) for p in polls)
    assert len({p.minute for p in polls}) > 30


def test_no_polls_at_night() -> None:
    for hour, minute in ((20, 45), (21, 0), (23, 30), (2, 0), (5, 0)):
        for poll in _next(hour, minute):
            assert schedule.QUIET_END <= poll.time() < schedule.QUIET_START
    # After an evening poll the next one is the following morning, 06:00-06:30.
    assert all(p.date().day == 4 and p.hour == 6 and p.minute < 30 for p in _next(21))
    # At 02:00 it is the same morning.
    assert all(p.date().day == 3 and p.hour == 6 for p in _next(2))


def test_daylight_saving_switch() -> None:
    # The night Sweden goes back to winter time (2026-10-25).
    assert all(p.hour == 6 and p.utcoffset() == timedelta(hours=1) for p in _next(21, day=24))
