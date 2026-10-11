"""Integration tests covering end-to-end sync behavior."""

from __future__ import annotations

from datetime import date

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.todo.entity import TodoItem
from homeassistant.components.todo.const import TodoItemStatus

from custom_components.ha_google_tasks_icloud_todo_sync import DomainData
from custom_components.ha_google_tasks_icloud_todo_sync.const import (
    ATTR_ENTRY_ID,
    CONF_DEBOUNCE_SECONDS,
    CONF_GOOGLE_ENTITY_ID,
    CONF_GROUP_NAME,
    CONF_HOME_ASSISTANT_ENTITY_ID,
    CONF_ICLOUD_ENTITY_ID,
    CONF_POLL_INTERVAL,
    DOMAIN,
    SERVICE_SYNC_NOW,
    STORAGE_KEY,
)


async def _setup_entry(hass, todo_lists) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        data={
            CONF_GROUP_NAME: "Kitchen",
            CONF_HOME_ASSISTANT_ENTITY_ID: todo_lists["entity_ids"]["home"],
            CONF_GOOGLE_ENTITY_ID: todo_lists["entity_ids"]["google"],
            CONF_ICLOUD_ENTITY_ID: todo_lists["entity_ids"]["icloud"],
        },
        options={
            CONF_POLL_INTERVAL: 60,
            CONF_DEBOUNCE_SECONDS: 0,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _by_summary(entity) -> dict[str, TodoItem]:
    return {item.summary: item for item in entity.todo_items or []}


async def test_bootstrap_syncs_union_and_propagates_changes(hass, todo_lists) -> None:
    await todo_lists["home"].async_create_todo_item(
        TodoItem(
            summary="Milk",
            status=TodoItemStatus.NEEDS_ACTION,
            due=date(2026, 10, 12),
            description=None,
        )
    )
    await todo_lists["google"].async_create_todo_item(
        TodoItem(
            summary="Bread",
            status=TodoItemStatus.NEEDS_ACTION,
            due=date(2026, 10, 13),
            description="Bakery",
        )
    )
    entry = await _setup_entry(hass, todo_lists)

    assert set(_by_summary(todo_lists["home"])) == {"Milk", "Bread"}
    assert set(_by_summary(todo_lists["google"])) == {"Milk", "Bread"}
    assert set(_by_summary(todo_lists["icloud"])) == {"Milk", "Bread"}

    google_milk = _by_summary(todo_lists["google"])["Milk"]
    await todo_lists["google"].async_external_update(
        google_milk.uid,
        description="2% organic",
        due=date(2026, 10, 20),
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC_NOW,
        {ATTR_ENTRY_ID: entry.entry_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    home_milk = _by_summary(todo_lists["home"])["Milk"]
    icloud_milk = _by_summary(todo_lists["icloud"])["Milk"]
    assert home_milk.description == "2% organic"
    assert icloud_milk.description == "2% organic"
    assert home_milk.due == date(2026, 10, 20)
    assert icloud_milk.due == date(2026, 10, 20)


async def test_delete_propagates_and_second_cycle_is_a_no_op(hass, todo_lists) -> None:
    for entity in (todo_lists["home"], todo_lists["google"], todo_lists["icloud"]):
        await entity.async_create_todo_item(
            TodoItem(summary="Milk", status=TodoItemStatus.NEEDS_ACTION)
        )
    entry = await _setup_entry(hass, todo_lists)

    google_milk = _by_summary(todo_lists["google"])["Milk"]
    await todo_lists["google"].async_external_delete(google_milk.uid)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC_NOW,
        {ATTR_ENTRY_ID: entry.entry_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert "Milk" not in _by_summary(todo_lists["home"])
    assert "Milk" not in _by_summary(todo_lists["icloud"])

    domain_data: DomainData = hass.data[DOMAIN]
    group = domain_data.store.get_group(entry.entry_id, settings={})
    assert len(group["tombstones"]) == 1

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC_NOW,
        {ATTR_ENTRY_ID: entry.entry_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert group["last_sync_result"]["deletes"] == 0


async def test_unavailable_endpoint_aborts_deletes(hass, todo_lists) -> None:
    for entity in (todo_lists["home"], todo_lists["google"], todo_lists["icloud"]):
        await entity.async_create_todo_item(
            TodoItem(summary="Milk", status=TodoItemStatus.NEEDS_ACTION)
        )
    entry = await _setup_entry(hass, todo_lists)

    google_milk = _by_summary(todo_lists["google"])["Milk"]
    await todo_lists["icloud"].async_set_available(False)
    await todo_lists["google"].async_external_delete(google_milk.uid)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC_NOW,
        {ATTR_ENTRY_ID: entry.entry_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert "Milk" in _by_summary(todo_lists["home"])
    assert entry.runtime_data.manager.status == "unavailable"


async def test_order_sync_moves_supported_endpoints_only(hass, todo_lists) -> None:
    for entity in (todo_lists["home"], todo_lists["google"], todo_lists["icloud"]):
        await entity.async_create_todo_item(
            TodoItem(summary="A", status=TodoItemStatus.NEEDS_ACTION)
        )
        await entity.async_create_todo_item(
            TodoItem(summary="B", status=TodoItemStatus.NEEDS_ACTION)
        )
    entry = await _setup_entry(hass, todo_lists)

    home_order = [item.uid for item in todo_lists["home"].todo_items]
    await todo_lists["home"].async_external_reorder(list(reversed(home_order)))
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC_NOW,
        {ATTR_ENTRY_ID: entry.entry_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert [item.summary for item in todo_lists["google"].todo_items] == ["B", "A"]
    assert [item.summary for item in todo_lists["icloud"].todo_items] == ["A", "B"]

