"""When to ask Vklass next: about every 1.5 hours by day, never at night.

The times are random so that the requests do not arrive like clockwork, and
two students in the same home do not sign in at the same moment. Kept free of
Home Assistant so the tests can load it on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import random
from zoneinfo import ZoneInfo

_SWEDEN = ZoneInfo("Europe/Stockholm")

# The gap between two polls by day, picked at random within this range.
# Vklass's session ends 2 h after its cookie was last issued, and a request
# re-issues it once it is at least 1 h old: gaps of 70-110 min keep one
# session all day, so the password is sent once each morning.
POLL_MIN = timedelta(minutes=70)
POLL_MAX = timedelta(minutes=110)
# No polls from QUIET_START to QUIET_END; the first poll after the night comes
# up to MORNING_SPREAD after QUIET_END.
QUIET_START = time(22, 0)
QUIET_END = time(6, 0)
MORNING_SPREAD = timedelta(minutes=30)


def _between(rng: random.Random, low: timedelta, high: timedelta) -> timedelta:
    return timedelta(seconds=rng.uniform(low.total_seconds(), high.total_seconds()))


def next_poll(now: datetime, rng: random.Random | None = None) -> datetime:
    """The time (UTC) of the poll after one made at `now` (timezone-aware)."""
    rng = rng or random.Random()
    nxt = now.astimezone(UTC) + _between(rng, POLL_MIN, POLL_MAX)
    local = nxt.astimezone(_SWEDEN)
    if QUIET_END <= local.time() < QUIET_START:
        return nxt
    morning = local.date() + timedelta(days=1) if local.time() >= QUIET_START else local.date()
    start = datetime.combine(morning, QUIET_END, _SWEDEN)
    return start.astimezone(UTC) + _between(rng, timedelta(0), MORNING_SPREAD)
