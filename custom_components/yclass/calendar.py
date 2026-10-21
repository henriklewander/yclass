"""Calendars per student: homework/tests, and days off."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import VKLASS_TZ
from .coordinator import YclassConfigEntry, YclassCoordinator
from .entity import YclassEntity
from .vklass import CalendarData, DayOff, Homework, SchoolEvent

_LOGGER = logging.getLogger(__name__)


def homework_event(hw: Homework) -> CalendarEvent:
    summary = f"{hw.title} ({hw.subject})" if hw.subject else hw.title
    details = [f"{hw.kind}, {hw.subject}" if hw.kind and hw.subject else hw.kind or hw.subject or ""]
    if hw.all_day:
        details.append(f"Datum: {hw.due:%Y-%m-%d %H:%M}")
    details += [hw.description, hw.url]
    if hw.all_day:
        start: date | datetime = hw.due.date()
        end: date | datetime = start + timedelta(days=1)
    else:
        start = hw.due.replace(tzinfo=VKLASS_TZ)
        end = start + timedelta(hours=1)
    return CalendarEvent(
        start=start,
        end=end,
        summary=summary,
        description="\n\n".join(d for d in details if d),
        uid=f"vklass-assignment-{hw.id}",
    )


def day_off_event(off: DayOff) -> CalendarEvent:
    return CalendarEvent(
        start=off.start,
        end=off.end + timedelta(days=1),
        summary=off.title,
        description=off.description or None,
        uid=f"vklass-dayoff-{off.start.isoformat()}-{off.title}",
    )


def school_event_event(ev: SchoolEvent) -> CalendarEvent:
    if ev.all_day:
        start: date | datetime = ev.start.date()
        end: date | datetime = (ev.end.date() if ev.end and ev.end.date() > start else start) + timedelta(days=1)
    else:
        start = ev.start.replace(tzinfo=VKLASS_TZ)
        end = ev.end.replace(tzinfo=VKLASS_TZ) if ev.end and ev.end > ev.start else start + timedelta(hours=1)
    return CalendarEvent(
        start=start,
        end=end,
        summary=ev.title,
        description="\n\n".join(d for d in (ev.description, ev.url) if d) or None,
        uid=f"vklass-event-{ev.start.isoformat()}-{ev.title}",
    )


def homework_events(data: CalendarData) -> list[CalendarEvent]:
    return [homework_event(hw) for hw in data.homework]


def day_off_events(data: CalendarData) -> list[CalendarEvent]:
    return [day_off_event(off) for off in data.days_off]


def school_events(data: CalendarData) -> list[CalendarEvent]:
    return [school_event_event(ev) for ev in data.events]


async def async_setup_entry(
    hass: HomeAssistant, entry: YclassConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            YclassCalendar(coordinator, "homework", homework_events),
            YclassCalendar(coordinator, "days_off", day_off_events),
            YclassCalendar(coordinator, "events", school_events),
        ]
    )


class YclassCalendar(YclassEntity, CalendarEntity):
    def __init__(
        self,
        coordinator: YclassCoordinator,
        key: str,
        to_events: Callable[[CalendarData], list[CalendarEvent]],
    ) -> None:
        super().__init__(coordinator, key)
        self._to_events = to_events

    @property
    def event(self) -> CalendarEvent | None:
        """The current or next event."""
        now = dt_util.now()
        upcoming = [ev for ev in self._to_events(self.coordinator.data) if ev.end_datetime_local > now]
        return min(upcoming, key=lambda ev: ev.start_datetime_local, default=None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        coordinator = self.coordinator
        data = coordinator.data
        first, last = start_date.date(), end_date.date()
        if first < coordinator.window_start or last > coordinator.window_end:
            try:
                data = await coordinator.fetch_range(first, last + timedelta(days=1))
            except UpdateFailed as err:
                _LOGGER.warning("Could not fetch %s..%s, showing cached events: %s", first, last, err)
        return [
            ev
            for ev in self._to_events(data)
            if ev.end_datetime_local > start_date and ev.start_datetime_local < end_date
        ]
