"""Polls Vklass for one student."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BLOCKED_BACKOFF_MAX,
    BLOCKED_BACKOFF_MIN,
    CONF_ICAL_URL,
    DOMAIN,
    WINDOW_AHEAD,
    WINDOW_PAST,
)
from .schedule import POLL_MAX, next_poll
from .vklass import Blocked, CalendarData, InvalidAuth, VklassClient, VklassError, fetch_ics

_LOGGER = logging.getLogger(__name__)

type YclassConfigEntry = ConfigEntry[YclassCoordinator]

SOURCE_VKLASS = "vklass"  # signed in to app.vklass.se: everything
SOURCE_ICAL = "ical"  # the calendar subscription: lessons and homework only
SOURCE_CACHE = "cache"  # neither answered: the last data we had


class YclassCoordinator(DataUpdateCoordinator[CalendarData]):
    """Keeps the homework and days off for WINDOW_PAST..WINDOW_AHEAD around today.

    Polls about every two hours at random times, and not at night (schedule.py).

    Signs in to Vklass when it can. While Vklass's bot protection blocks the
    sign-in, it waits BLOCKED_BACKOFF_MIN, doubling up to BLOCKED_BACKOFF_MAX,
    before trying again, and meanwhile reads the calendar subscription (if one
    is configured). Lunch, days off and school events are not in the
    subscription; those are kept from the last successful sign-in.
    """

    config_entry: YclassConfigEntry

    def __init__(self, hass: HomeAssistant, entry: YclassConfigEntry, client: VklassClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=POLL_MAX,  # replaced by a random gap on every poll
        )
        self.client = client
        self.window_start = date.min
        self.window_end = date.min
        self.source: str | None = None
        self.vklass_updated: datetime | None = None  # last successful sign-in and fetch
        self.blocked_until: datetime | None = None
        self.last_error: str | None = None
        self._backoff = BLOCKED_BACKOFF_MIN
        # The client logs in again when its session has expired; keep two
        # callers from doing that at the same time.
        self._lock = asyncio.Lock()

    @property
    def ical_url(self) -> str | None:
        return self.config_entry.options.get(CONF_ICAL_URL) or None

    async def fetch_range(self, start: date, end: date, *, lunch: bool = False) -> CalendarData:
        if self.blocked_until and dt_util.utcnow() < self.blocked_until:
            raise UpdateFailed(f"Vklass blocks sign-in; next try {dt_util.as_local(self.blocked_until):%H:%M}")
        try:
            async with self._lock:
                data = await self.client.fetch(start, end, lunch=lunch)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Blocked as err:
            self.blocked_until = dt_util.utcnow() + self._backoff
            self._backoff = min(self._backoff * 2, BLOCKED_BACKOFF_MAX)
            raise UpdateFailed(f"Vklass: {err}") from err
        except (VklassError, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Vklass: {str(err) or type(err).__name__}") from err
        self.blocked_until = None
        self._backoff = BLOCKED_BACKOFF_MIN
        return data

    async def _async_update_data(self) -> CalendarData:
        # Set before anything can fail: HA schedules the next poll from it either way.
        now = dt_util.utcnow()
        self.update_interval = next_poll(now) - now
        today = dt_util.now().date()
        start, end = today - WINDOW_PAST, today + WINDOW_AHEAD
        errors: list[str] = []

        try:
            data = await self.fetch_range(start, end, lunch=True)
        except UpdateFailed as err:
            errors.append(str(err))
        else:
            self.window_start, self.window_end = start, end
            self.vklass_updated = dt_util.utcnow()
            return self._done(SOURCE_VKLASS, data)

        if self.ical_url:
            try:
                data = await fetch_ics(async_get_clientsession(self.hass), self.ical_url)
            except (VklassError, aiohttp.ClientError, TimeoutError, ValueError) as err:
                errors.append(f"Calendar subscription: {str(err) or type(err).__name__}")
            else:
                return self._done(SOURCE_ICAL, self._fill_in(data), "; ".join(errors))

        if self.data is not None:
            return self._done(SOURCE_CACHE, self.data, "; ".join(errors))
        raise UpdateFailed("; ".join(errors))

    def _done(self, source: str, data: CalendarData, error: str | None = None) -> CalendarData:
        if error and error != self.last_error:
            _LOGGER.warning("%s: using %s data. %s", self.name, source, error)
        self.source, self.last_error = source, error
        return data

    def _fill_in(self, data: CalendarData) -> CalendarData:
        """Add what the subscription lacks from earlier data: homework texts,
        kind and subject (same assignment ids), lunch, days off and events."""
        previous = self.data or CalendarData()
        known = {hw.id: hw for hw in previous.homework}
        homework = [
            replace(
                hw,
                description=old.description or hw.description,
                kind=old.kind or hw.kind,
                subject=old.subject or hw.subject,
                url=old.url,
            )
            if (old := known.get(hw.id))
            else hw
            for hw in data.homework
        ]
        return replace(
            data,
            homework=homework,
            lunch=previous.lunch,
            days_off=previous.days_off,
            events=data.events or previous.events,
        )
