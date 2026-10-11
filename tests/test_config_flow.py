"""Config-flow coverage."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries

from custom_components.ha_google_tasks_icloud_todo_sync.const import (
    CONF_GOOGLE_ENTITY_ID,
    CONF_GROUP_NAME,
    CONF_HOME_ASSISTANT_ENTITY_ID,
    CONF_ICLOUD_ENTITY_ID,
    DOMAIN,
)


async def test_config_flow_rejects_overlap_and_creates_entry(hass, todo_lists) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_GROUP_NAME: "Kitchen",
            CONF_HOME_ASSISTANT_ENTITY_ID: todo_lists["entity_ids"]["home"],
            CONF_GOOGLE_ENTITY_ID: todo_lists["entity_ids"]["google"],
            CONF_ICLOUD_ENTITY_ID: todo_lists["entity_ids"]["icloud"],
        },
    )
    assert result["type"] == "create_entry"

    existing = MockConfigEntry(
        domain=DOMAIN,
        title="Existing",
        data=result["data"],
        options=result["options"],
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_GROUP_NAME: "Duplicate",
            CONF_HOME_ASSISTANT_ENTITY_ID: todo_lists["entity_ids"]["home"],
            CONF_GOOGLE_ENTITY_ID: todo_lists["entity_ids"]["google"],
            CONF_ICLOUD_ENTITY_ID: todo_lists["entity_ids"]["icloud"],
        },
    )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "entity_already_configured"

