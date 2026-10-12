"""Shared test fixtures."""

from __future__ import annotations

import inspect
import sys
from dataclasses import fields, replace
from datetime import date, datetime
from pathlib import Path

import pytest
from homeassistant.components.todo.const import (
    DATA_COMPONENT,
    TodoItemStatus,
    TodoListEntityFeature,
)
from homeassistant.setup import async_setup_component

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from homeassistant.components.todo.entity import TodoItem, TodoListEntity
except ModuleNotFoundError:  # pragma: no cover
    from homeassistant.components.todo import TodoItem, TodoListEntity

_TODOITEM_FIELDS = {field.name for field in fields(TodoItem)}


def _make_todo_item(
    *,
    uid: str | None,
    summary: str | None,
    status,
    due,
    description: str | None,
    completed=None,
) -> TodoItem:
    kwargs = {
        "uid": uid,
        "summary": summary,
        "status": status,
        "due": due,
        "description": description,
    }
    if "completed" in _TODOITEM_FIELDS:
        kwargs["completed"] = completed
    return TodoItem(**kwargs)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the custom integration under test."""
    yield


@pytest.fixture(autouse=True)
async def unload_config_entries(hass):
    """Unload config entries after each test to avoid lingering timers."""
    yield
    for entry in list(hass.config_entries.async_entries()):
        await hass.config_entries.async_remove(entry.entry_id)


class FakeTodoListEntity(TodoListEntity):
    """In-memory todo entity used by tests."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        name: str,
        unique_id: str,
        features: TodoListEntityFeature,
        items: list[TodoItem] | None = None,
        date_only: bool = False,
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_supported_features = features
        self.entity_id = f"todo.{unique_id}"
        self._available = True
        self._counter = 0
        self._date_only = date_only
        self._items: list[TodoItem] = []
        for item in items or []:
            self._counter += 1
            uid = item.uid or f"{unique_id}-{self._counter}"
            self._items.append(
                replace(item, uid=uid, due=self._normalize_due(item.due))
            )
        self._sync_attr()

    @property
    def available(self) -> bool:
        return self._available

    def _sync_attr(self) -> None:
        self._attr_todo_items = [replace(item) for item in self._items]

    def _normalize_due(self, due):
        if self._date_only and isinstance(due, datetime):
            return due.date()
        return due

    async def async_create_todo_item(self, item: TodoItem) -> None:
        self._counter += 1
        self._items.append(
            _make_todo_item(
                uid=f"{self._attr_unique_id}-{self._counter}",
                summary=item.summary,
                status=item.status,
                due=self._normalize_due(item.due),
                description=item.description,
                completed=getattr(item, "completed", None),
            )
        )
        self._sync_attr()
        self.async_write_ha_state()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        for index, existing in enumerate(self._items):
            if existing.uid != item.uid:
                continue
            completed = (
                datetime(2026, 10, 11, 12, 0, 0)
                if item.status == TodoItemStatus.COMPLETED
                else None
            )
            self._items[index] = _make_todo_item(
                uid=existing.uid,
                summary=item.summary,
                status=item.status,
                due=self._normalize_due(item.due),
                description=item.description,
                completed=completed,
            )
            break
        self._sync_attr()
        self.async_write_ha_state()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        self._items = [item for item in self._items if item.uid not in uids]
        self._sync_attr()
        self.async_write_ha_state()

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        src_index = next(
            index for index, item in enumerate(self._items) if item.uid == uid
        )
        item = self._items.pop(src_index)
        if previous_uid is None:
            self._items.insert(0, item)
        else:
            dst_index = next(
                index
                for index, existing in enumerate(self._items)
                if existing.uid == previous_uid
            )
            self._items.insert(dst_index + 1, item)
        self._sync_attr()
        self.async_write_ha_state()

    async def async_external_update(
        self,
        uid: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        due: date | datetime | None = None,
        status: TodoItemStatus | None = None,
    ) -> None:
        """Simulate an external provider edit."""
        for index, existing in enumerate(self._items):
            if existing.uid != uid:
                continue
            self._items[index] = _make_todo_item(
                uid=existing.uid,
                summary=summary if summary is not None else existing.summary,
                status=status if status is not None else existing.status,
                due=self._normalize_due(due if due is not None else existing.due),
                description=description
                if description is not None
                else existing.description,
                completed=getattr(existing, "completed", None),
            )
            break
        self._sync_attr()
        self.async_write_ha_state()

    async def async_external_delete(self, uid: str) -> None:
        await self.async_delete_todo_items([uid])

    async def async_external_reorder(self, ordered_uids: list[str]) -> None:
        uid_map = {item.uid: item for item in self._items}
        self._items = [uid_map[uid] for uid in ordered_uids]
        self._sync_attr()
        self.async_write_ha_state()

    async def async_set_available(self, available: bool) -> None:
        self._available = available
        self.async_write_ha_state()


@pytest.fixture
async def todo_lists(hass):
    """Create three fake todo entities."""
    await async_setup_component(hass, "todo", {})
    component = hass.data[DATA_COMPONENT]
    features_full = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )
    features_google = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )
    features_icloud = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    home = FakeTodoListEntity(
        name="Home Assistant Tasks",
        unique_id="home",
        features=features_full,
    )
    google = FakeTodoListEntity(
        name="Google Tasks",
        unique_id="google",
        features=features_google,
        date_only=True,
    )
    icloud = FakeTodoListEntity(
        name="iCloud Reminders",
        unique_id="icloud",
        features=features_icloud,
    )
    add_result = component.async_add_entities([home, google, icloud])
    if inspect.isawaitable(add_result):
        await add_result
    await hass.async_block_till_done()
    return {
        "home": home,
        "google": google,
        "icloud": icloud,
        "entity_ids": {
            "home": home.entity_id,
            "google": google.entity_id,
            "icloud": icloud.entity_id,
        },
    }
