"""Tests for todo entity adapter behavior."""

from __future__ import annotations

import pytest
from homeassistant.components.todo.const import TodoItemStatus
from homeassistant.exceptions import HomeAssistantError

from custom_components.ha_google_tasks_icloud_todo_sync import adapter as adapter_module
from custom_components.ha_google_tasks_icloud_todo_sync.const import STATUS_NEEDS_ACTION
from custom_components.ha_google_tasks_icloud_todo_sync.models import CanonicalTodoItem

try:
    from homeassistant.components.todo.entity import TodoItem
except ModuleNotFoundError:  # pragma: no cover
    from homeassistant.components.todo import TodoItem


def _record(summary: str) -> CanonicalTodoItem:
    return CanonicalTodoItem(summary=summary, status=STATUS_NEEDS_ACTION)


async def test_create_retries_after_refresh_failure(
    hass, todo_lists, monkeypatch
) -> None:
    monkeypatch.setattr(adapter_module, "CREATE_UID_DELAY", 0)
    entity = todo_lists["home"]
    create_todo_item = entity.async_create_todo_item

    async def create_without_publishing(item: TodoItem) -> None:
        await create_todo_item(item)
        entity._attr_todo_items = []

    refresh_count = 0

    async def refresh(*, force_refresh: bool = False) -> None:
        nonlocal refresh_count
        refresh_count += 1
        if refresh_count == 1:
            raise RuntimeError("temporary refresh failure")
        entity._sync_attr()

    monkeypatch.setattr(entity, "async_create_todo_item", create_without_publishing)
    monkeypatch.setattr(entity, "async_update_ha_state", refresh)

    uid = await adapter_module.TodoSyncAdapter(hass).async_create(
        entity.entity_id, _record("Milk")
    )

    assert uid == entity._items[0].uid
    assert refresh_count == 2


async def test_create_fails_after_refresh_retries_are_exhausted(
    hass, todo_lists, monkeypatch
) -> None:
    monkeypatch.setattr(adapter_module, "CREATE_UID_DELAY", 0)
    entity = todo_lists["home"]
    create_todo_item = entity.async_create_todo_item

    async def create_without_publishing(item: TodoItem) -> None:
        await create_todo_item(item)
        entity._attr_todo_items = []

    refresh_count = 0

    async def refresh(*, force_refresh: bool = False) -> None:
        nonlocal refresh_count
        refresh_count += 1
        raise RuntimeError("refresh unavailable")

    monkeypatch.setattr(entity, "async_create_todo_item", create_without_publishing)
    monkeypatch.setattr(entity, "async_update_ha_state", refresh)

    with pytest.raises(HomeAssistantError, match="Unable to determine created uid"):
        await adapter_module.TodoSyncAdapter(hass).async_create(
            entity.entity_id, _record("Milk")
        )

    assert refresh_count == adapter_module.CREATE_UID_ATTEMPTS - 1
    assert len(entity._items) == 1


async def test_create_rejects_multiple_matching_new_uids(
    hass, todo_lists, monkeypatch
) -> None:
    entity = todo_lists["home"]
    create_todo_item = entity.async_create_todo_item

    async def create_duplicate_items(item: TodoItem) -> None:
        await create_todo_item(item)
        await create_todo_item(
            TodoItem(summary=item.summary, status=TodoItemStatus.NEEDS_ACTION)
        )

    monkeypatch.setattr(entity, "async_create_todo_item", create_duplicate_items)

    with pytest.raises(HomeAssistantError, match="Unable to uniquely determine"):
        await adapter_module.TodoSyncAdapter(hass).async_create(
            entity.entity_id, _record("Milk")
        )
