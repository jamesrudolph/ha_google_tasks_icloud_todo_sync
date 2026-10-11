"""Config flow for the Google Tasks ↔ iCloud Reminders sync integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_CLEANUP_DAYS,
    CONF_CLEANUP_ENABLED,
    CONF_DEBOUNCE_SECONDS,
    CONF_GOOGLE_ENTITY_ID,
    CONF_ICLOUD_ENTITY_ID,
    CONF_NAME,
    CONF_PAIR_ID,
    CONF_SYNC_COMPLETION,
    CONF_SYNC_DESCRIPTIONS,
    CONF_SYNC_DIRECTION,
    CONF_SYNC_DUE_DATES,
    DEFAULT_CLEANUP_DAYS,
    DEFAULT_CLEANUP_ENABLED,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_SYNC_COMPLETION,
    DEFAULT_SYNC_DESCRIPTIONS,
    DEFAULT_SYNC_DIRECTION,
    DEFAULT_SYNC_DUE_DATES,
    DOMAIN,
    DIRECTION_BOTH,
    DIRECTION_GOOGLE_TO_ICLOUD,
    DIRECTION_ICLOUD_TO_GOOGLE,
)

DIRECTION_OPTIONS = [
    selector.SelectOptionDict(value=DIRECTION_BOTH, label="Bidirectional"),
    selector.SelectOptionDict(
        value=DIRECTION_GOOGLE_TO_ICLOUD, label="Google Tasks → iCloud Reminders"
    ),
    selector.SelectOptionDict(
        value=DIRECTION_ICLOUD_TO_GOOGLE, label="iCloud Reminders → Google Tasks"
    ),
]


def _build_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(
                CONF_GOOGLE_ENTITY_ID,
                default=defaults.get(CONF_GOOGLE_ENTITY_ID),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="todo")
            ),
            vol.Required(
                CONF_ICLOUD_ENTITY_ID,
                default=defaults.get(CONF_ICLOUD_ENTITY_ID),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="todo")
            ),
            vol.Required(
                CONF_SYNC_DIRECTION,
                default=defaults.get(CONF_SYNC_DIRECTION, DEFAULT_SYNC_DIRECTION),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=DIRECTION_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(
                CONF_SYNC_COMPLETION,
                default=defaults.get(CONF_SYNC_COMPLETION, DEFAULT_SYNC_COMPLETION),
            ): bool,
            vol.Required(
                CONF_SYNC_DUE_DATES,
                default=defaults.get(CONF_SYNC_DUE_DATES, DEFAULT_SYNC_DUE_DATES),
            ): bool,
            vol.Required(
                CONF_SYNC_DESCRIPTIONS,
                default=defaults.get(CONF_SYNC_DESCRIPTIONS, DEFAULT_SYNC_DESCRIPTIONS),
            ): bool,
            vol.Required(
                CONF_CLEANUP_ENABLED,
                default=defaults.get(CONF_CLEANUP_ENABLED, DEFAULT_CLEANUP_ENABLED),
            ): bool,
            vol.Required(
                CONF_CLEANUP_DAYS,
                default=defaults.get(CONF_CLEANUP_DAYS, DEFAULT_CLEANUP_DAYS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=3650, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_DEBOUNCE_SECONDS,
                default=defaults.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=300, mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )


def _validate_input(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if user_input[CONF_GOOGLE_ENTITY_ID] == user_input[CONF_ICLOUD_ENTITY_ID]:
        errors["base"] = "same_entity"
    return errors


class GoogleTasksIcloudRemindersSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the sync integration."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_input(user_input)
            if not errors:
                unique_id = f"{user_input[CONF_GOOGLE_ENTITY_ID]}::{user_input[CONF_ICLOUD_ENTITY_ID]}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                data = dict(user_input)
                data[CONF_PAIR_ID] = slugify(user_input[CONF_NAME])
                return self.async_create_entry(title=user_input[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle sync pair options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the pair settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_input(user_input)
            if not errors:
                data = dict(self.config_entry.data)
                data.update(user_input)
                data[CONF_PAIR_ID] = self.config_entry.data[CONF_PAIR_ID]
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    title=user_input[CONF_NAME],
                    data=data,
                )
                return self.async_create_entry(title="", data={})

        defaults = dict(self.config_entry.data)
        defaults.update(self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(defaults),
            errors=errors,
        )
