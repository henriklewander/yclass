"""yclass: homework from Vklass (Göteborgs Stad) in Home Assistant."""

from __future__ import annotations

from pathlib import Path

import aiohttp

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import CARD_URL, DOMAIN
from .coordinator import YclassConfigEntry, YclassCoordinator
from .vklass import VklassClient, new_cookie_jar

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card and load it on every dashboard."""
    integration = await async_get_integration(hass, DOMAIN)
    card = Path(__file__).parent / "frontend" / "yclass-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), False)])
    # The version in the URL makes browsers fetch the card again after an update.
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YclassConfigEntry) -> bool:
    # Each student needs a session of their own: the cookies are the login.
    # A plain aiohttp session, since HA's helper sessions may not be closed
    # and would otherwise pile up on every reload.
    session = aiohttp.ClientSession(cookie_jar=new_cookie_jar())
    entry.async_on_unload(session.close)
    client = VklassClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = YclassCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YclassConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
