"""Sensors per student: upcoming homework/tests, and the coming school day."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DAY_SWITCH, VKLASS_TZ
from .coordinator import YclassConfigEntry
from .entity import YclassEntity
from .vklass import Homework, SchoolDay

# How many school days the schoolday sensor lists in its `days` attribute.
DAYS_LISTED = 10
# How far ahead the `agenda` attribute (days off, school events) reaches.
AGENDA_DAYS = 30


def reference_date(now: datetime) -> date:
    """The first day that still matters: today, or tomorrow from DAY_SWITCH on."""
    now = now.astimezone(VKLASS_TZ)
    return now.date() + timedelta(days=1) if now.time() >= DAY_SWITCH else now.date()


async def async_setup_entry(
    hass: HomeAssistant, entry: YclassConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            YclassHomeworkSensor(coordinator, "homework"),
            YclassSchoolDaySensor(coordinator, "schoolday"),
        ]
    )


class YclassHomeworkSensor(YclassEntity, SensorEntity):
    _attr_native_unit_of_measurement = "st"
    # The list can be long; the history only needs the count.
    _unrecorded_attributes = frozenset({"homework", "next_description"})

    def _upcoming(self) -> list[Homework]:
        today = dt_util.now().date()
        return [hw for hw in self.coordinator.data.homework if hw.due.date() >= today]

    @property
    def native_value(self) -> int:
        return len(self._upcoming())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        upcoming = self._upcoming()
        attrs: dict[str, Any] = {"homework": [hw.as_dict() for hw in upcoming]}
        if upcoming:
            nxt = upcoming[0]
            attrs |= {
                "next_title": nxt.title,
                "next_kind": nxt.kind,
                "next_subject": nxt.subject,
                "next_due": nxt.due.isoformat(timespec="minutes"),
                "next_description": nxt.description,
            }
        return attrs


class YclassSchoolDaySensor(YclassEntity, SensorEntity):
    """When the coming school day starts; ends, lunch and the next days as attributes.

    "Coming" is today until DAY_SWITCH (17:00), after that the next day with
    lessons. The state is rewritten at DAY_SWITCH and at midnight, so the
    switch does not wait for the next poll.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"days", "lunch", "pe", "agenda", "first_name", "reference_date"})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._day_switched, hour=[0, DAY_SWITCH.hour], minute=DAY_SWITCH.minute, second=0
            )
        )

    @callback
    def _day_switched(self, _now: datetime) -> None:
        self.async_write_ha_state()

    def _days(self) -> list[SchoolDay]:
        return self.coordinator.data.school_days(reference_date(dt_util.now()))

    @property
    def native_value(self) -> datetime | None:
        days = self._days()
        return days[0].start.replace(tzinfo=VKLASS_TZ) if days else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        student = self.coordinator.client.student
        days = self._days()
        ref = reference_date(dt_util.now())
        attrs: dict[str, Any] = {
            "first_name": student.first_name if student else None,
            "reference_date": ref.isoformat(),
            "days": [day.as_dict() for day in days[:DAYS_LISTED]],
            "agenda": self.coordinator.data.agenda(ref, ref + timedelta(days=AGENDA_DAYS)),
        }
        if days:
            first = days[0]
            attrs |= {
                "date": first.day.isoformat(),
                "start": f"{first.start:%H:%M}",
                "end": f"{first.end:%H:%M}",
                "lunch": first.lunch,
                "pe": first.pe_times(),
            }
        return attrs
