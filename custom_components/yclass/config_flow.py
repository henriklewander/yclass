"""Config flow: one entry per student, signed in with their Göteborgs Stad account."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ICAL_URL, DOMAIN
from .vklass import InvalidAuth, Student, VklassClient, VklassError, fetch_ics, new_cookie_jar

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})
OPTIONS_SCHEMA = vol.Schema({vol.Optional(CONF_ICAL_URL): str})


class YclassOptionsFlow(OptionsFlowWithReload):
    """The calendar subscription used while Vklass cannot be signed in to."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = (user_input.get(CONF_ICAL_URL) or "").strip().replace("webcal://", "https://", 1)
            if url:
                try:
                    await fetch_ics(async_get_clientsession(self.hass), url)
                except (VklassError, aiohttp.ClientError, TimeoutError, ValueError):
                    _LOGGER.exception("Could not read the calendar subscription")
                    errors[CONF_ICAL_URL] = "invalid_ical"
            if not errors:
                return self.async_create_entry(data={CONF_ICAL_URL: url} if url else {})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, user_input or self.config_entry.options),
            # hassfest does not allow URLs in the translations themselves.
            description_placeholders={"example": "https://cal.vklass.se/….ics?custodian=true"},
            errors=errors,
        )


class YclassConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> YclassOptionsFlow:
        return YclassOptionsFlow()

    async def _try_login(self, username: str, password: str) -> tuple[Student | None, dict[str, str]]:
        try:
            async with aiohttp.ClientSession(cookie_jar=new_cookie_jar()) as session:
                return await VklassClient(session, username, password).login(), {}
        except InvalidAuth:
            return None, {"base": "invalid_auth"}
        except (VklassError, aiohttp.ClientError, TimeoutError):
            _LOGGER.exception("Could not sign in to Vklass")
            return None, {"base": "cannot_connect"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip().lower()
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()
            student, errors = await self._try_login(username, user_input[CONF_PASSWORD])
            if student is not None:
                return self.async_create_entry(
                    title=student.full_name or username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            student, errors = await self._try_login(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if student is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
