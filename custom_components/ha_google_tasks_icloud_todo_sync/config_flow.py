"""Config flow for the sync integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.todo.const import DATA_COMPONENT, TodoListEntityFeature
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

try:
    from homeassistant.config_entries import OptionsFlowWithReload
except ImportError:  # pragma: no cover - compatibility for older HA in local tests
    from homeassistant.config_entries import OptionsFlow

    class OptionsFlowWithReload(OptionsFlow):
        """Fallback for older Home Assistant test environments."""


from .const import (
    CONF_DEBOUNCE_SECONDS,
    CONF_GOOGLE_ENTITY_ID,
    CONF_GROUP_NAME,
    CONF_HOME_ASSISTANT_ENTITY_ID,
    CONF_ICLOUD_ENTITY_ID,
    CONF_MASS_DELETE_COUNT,
    CONF_MASS_DELETE_PERCENT,
    CONF_ORDER_SYNC,
    CONF_POLL_INTERVAL,
    CONF_TIEBREAK_ORDER,
    CONF_TOMBSTONE_DAYS,
    CONF_UNAVAILABLE_GRACE_MINUTES,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_MASS_DELETE_COUNT,
    DEFAULT_MASS_DELETE_PERCENT,
    DEFAULT_ORDER_SYNC,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DEFAULT_TIEBREAK_ORDER,
    DEFAULT_TOMBSTONE_DAYS,
    DEFAULT_UNAVAILABLE_GRACE_MINUTES,
    DOMAIN,
    ENDPOINT_GOOGLE,
    ENDPOINT_HOME_ASSISTANT,
    ENDPOINT_ICLOUD,
)


def _default_options() -> dict[str, Any]:
    return {
        CONF_TIEBREAK_ORDER: list(DEFAULT_TIEBREAK_ORDER),
        CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL_MINUTES,
        CONF_DEBOUNCE_SECONDS: DEFAULT_DEBOUNCE_SECONDS,
        CONF_TOMBSTONE_DAYS: DEFAULT_TOMBSTONE_DAYS,
        CONF_MASS_DELETE_COUNT: DEFAULT_MASS_DELETE_COUNT,
        CONF_MASS_DELETE_PERCENT: DEFAULT_MASS_DELETE_PERCENT,
        CONF_ORDER_SYNC: DEFAULT_ORDER_SYNC,
        CONF_UNAVAILABLE_GRACE_MINUTES: DEFAULT_UNAVAILABLE_GRACE_MINUTES,
    }


def _base_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_GROUP_NAME,
                default=user_input.get(CONF_GROUP_NAME, ""),
            ): str,
            vol.Required(
                CONF_HOME_ASSISTANT_ENTITY_ID,
                default=user_input.get(CONF_HOME_ASSISTANT_ENTITY_ID),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
            vol.Required(
                CONF_GOOGLE_ENTITY_ID,
                default=user_input.get(CONF_GOOGLE_ENTITY_ID),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
            vol.Required(
                CONF_ICLOUD_ENTITY_ID,
                default=user_input.get(CONF_ICLOUD_ENTITY_ID),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
        }
    )


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    endpoints = [ENDPOINT_HOME_ASSISTANT, ENDPOINT_ICLOUD, ENDPOINT_GOOGLE]
    return vol.Schema(
        {
            vol.Required(
                "priority_1",
                default=options[CONF_TIEBREAK_ORDER][0],
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=endpoints)
            ),
            vol.Required(
                "priority_2",
                default=options[CONF_TIEBREAK_ORDER][1],
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=endpoints)
            ),
            vol.Required(
                "priority_3",
                default=options[CONF_TIEBREAK_ORDER][2],
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=endpoints)
            ),
            vol.Required(
                CONF_POLL_INTERVAL,
                default=options[CONF_POLL_INTERVAL],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=60,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEBOUNCE_SECONDS,
                default=options[CONF_DEBOUNCE_SECONDS],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=300,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_TOMBSTONE_DAYS,
                default=options[CONF_TOMBSTONE_DAYS],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=365,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MASS_DELETE_COUNT,
                default=options[CONF_MASS_DELETE_COUNT],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=1000,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MASS_DELETE_PERCENT,
                default=options[CONF_MASS_DELETE_PERCENT],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=100,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_UNAVAILABLE_GRACE_MINUTES,
                default=options[CONF_UNAVAILABLE_GRACE_MINUTES],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=1440,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_ORDER_SYNC,
                default=options[CONF_ORDER_SYNC],
            ): bool,
        }
    )


def _selected_entities(data: dict[str, Any]) -> list[str]:
    return [
        data[CONF_HOME_ASSISTANT_ENTITY_ID],
        data[CONF_GOOGLE_ENTITY_ID],
        data[CONF_ICLOUD_ENTITY_ID],
    ]


def _validate_entity_capabilities(hass, entity_id: str) -> bool:
    component = hass.data.get(DATA_COMPONENT)
    if component is None or (entity := component.get_entity(entity_id)) is None:
        return False
    features = entity.supported_features or 0
    required = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )
    return bool(features & required == required)


class SyncConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create sync groups."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entities = _selected_entities(user_input)
            if len(set(entities)) != len(entities):
                errors["base"] = "duplicate_entities"
            elif any(
                not _validate_entity_capabilities(self.hass, entity_id)
                for entity_id in entities
            ):
                errors["base"] = "unsupported_entities"
            elif self._entity_overlap(entities):
                errors["base"] = "entity_already_configured"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_GROUP_NAME],
                    data=user_input,
                    options=_default_options(),
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_base_schema(user_input),
            errors=errors,
        )

    def _entity_overlap(self, entities: list[str]) -> bool:
        for existing in self._async_current_entries():
            existing_entities = _selected_entities(existing.data)
            if set(entities) & set(existing_entities):
                return True
        return False

    @staticmethod
    def async_get_options_flow(config_entry):
        return SyncOptionsFlow(config_entry)


class SyncOptionsFlow(OptionsFlowWithReload):
    """Options flow for sync groups."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = {**_default_options(), **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            priorities = [
                user_input.pop("priority_1"),
                user_input.pop("priority_2"),
                user_input.pop("priority_3"),
            ]
            if len(set(priorities)) != 3:
                errors["base"] = "duplicate_tiebreak"
            else:
                user_input[CONF_TIEBREAK_ORDER] = priorities
                return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(options),
            errors=errors,
        )
