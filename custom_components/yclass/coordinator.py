"""Polls Vklass for one student."""

from __future__ import annotations

import asyncio
from datetime import date
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, UPDATE_INTERVAL, WINDOW_AHEAD, WINDOW_PAST
from .vklass import CalendarData, InvalidAuth, VklassClient, VklassError

_LOGGER = logging.getLogger(__name__)

type YclassConfigEntry = ConfigEntry[YclassCoordinator]


class YclassCoordinator(DataUpdateCoordinator[CalendarData]):
    """Keeps the homework and days off for WINDOW_PAST..WINDOW_AHEAD around today."""

    config_entry: YclassConfigEntry

    def __init__(self, hass: HomeAssistant, entry: YclassConfigEntry, client: VklassClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.window_start = date.min
        self.window_end = date.min
        # The client logs in again when its session has expired; keep two
        # callers from doing that at the same time.
        self._lock = asyncio.Lock()

    async def fetch_range(self, start: date, end: date, *, lunch: bool = False) -> CalendarData:
        try:
            async with self._lock:
                return await self.client.fetch(start, end, lunch=lunch)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (VklassError, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Vklass: {err}") from err

    async def _async_update_data(self) -> CalendarData:
        today = dt_util.now().date()
        start, end = today - WINDOW_PAST, today + WINDOW_AHEAD
        data = await self.fetch_range(start, end, lunch=True)
        self.window_start, self.window_end = start, end
        return data
