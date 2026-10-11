"""Adapter for interacting with generic Home Assistant todo entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from homeassistant.components.todo.const import (
    DATA_COMPONENT,
    TodoItemStatus,
    TodoListEntityFeature,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import STATUS_COMPLETED, STATUS_NEEDS_ACTION
from .models import CanonicalTodoItem, EndpointSnapshotItem

try:
    from homeassistant.components.todo.entity import TodoItem, TodoListEntity
except ModuleNotFoundError:  # pragma: no cover
    from homeassistant.components.todo import TodoItem, TodoListEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EndpointCapabilities:
    """Capabilities exposed by one todo entity."""

    create: bool
    update: bool
    delete: bool
    move: bool
    due: bool
    description: bool


@dataclass(slots=True)
class EndpointSnapshot:
    """Current live data for one endpoint."""

    entity_id: str
    items: list[EndpointSnapshotItem]
    capabilities: EndpointCapabilities


def _normalize_due(value: date | datetime | None) -> str | None:
    """Normalize due values to ISO dates for v1."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _normalize_description(value: str | None) -> str | None:
    """Normalize empty descriptions away."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _to_record(item: TodoItem) -> CanonicalTodoItem:
    """Convert a Home Assistant TodoItem to the normalized record model."""
    return CanonicalTodoItem(
        summary=item.summary or "",
        status=STATUS_COMPLETED
        if item.status == TodoItemStatus.COMPLETED
        else STATUS_NEEDS_ACTION,
        due=_normalize_due(item.due),
        description=_normalize_description(item.description),
    )


def _to_todo_item(record: CanonicalTodoItem) -> TodoItem:
    """Convert the normalized record back into a TodoItem."""
    due = date.fromisoformat(record.due) if record.due is not None else None
    return TodoItem(
        summary=record.summary,
        status=TodoItemStatus.COMPLETED
        if record.status == STATUS_COMPLETED
        else TodoItemStatus.NEEDS_ACTION,
        due=due,
        description=record.description,
    )


class TodoSyncAdapter:
    """Read and write generic todo entities."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the adapter."""
        self.hass = hass

    def _get_entity(self, entity_id: str) -> TodoListEntity:
        component = self.hass.data.get(DATA_COMPONENT)
        if component is None or (entity := component.get_entity(entity_id)) is None:
            raise HomeAssistantError(f"Todo entity not found: {entity_id}")
        return entity

    def _get_capabilities(self, entity: TodoListEntity) -> EndpointCapabilities:
        features = entity.supported_features or 0
        return EndpointCapabilities(
            create=bool(features & TodoListEntityFeature.CREATE_TODO_ITEM),
            update=bool(features & TodoListEntityFeature.UPDATE_TODO_ITEM),
            delete=bool(features & TodoListEntityFeature.DELETE_TODO_ITEM),
            move=bool(features & TodoListEntityFeature.MOVE_TODO_ITEM),
            due=bool(
                features & TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
                or features & TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
            ),
            description=bool(features & TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM),
        )

    async def async_fetch(
        self, endpoint: str, entity_id: str
    ) -> EndpointSnapshot | None:
        """Return the current snapshot for an endpoint, or None if unavailable."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}:
            return None

        entity = self._get_entity(entity_id)
        if not entity.available or entity.todo_items is None:
            return None

        snapshot_items: list[EndpointSnapshotItem] = []
        for position, item in enumerate(entity.todo_items):
            if item.uid is None:
                _LOGGER.warning("Skipping item without uid on %s", entity_id)
                continue
            snapshot_items.append(
                EndpointSnapshotItem(
                    endpoint=endpoint,
                    uid=item.uid,
                    record=_to_record(item),
                    position=position,
                )
            )

        return EndpointSnapshot(
            entity_id=entity_id,
            items=snapshot_items,
            capabilities=self._get_capabilities(entity),
        )

    async def async_create(self, entity_id: str, record: CanonicalTodoItem) -> str:
        """Create an item and return its new uid."""
        entity = self._get_entity(entity_id)
        before = [item.uid for item in entity.todo_items or [] if item.uid is not None]
        await entity.async_create_todo_item(_to_todo_item(record))
        await self.hass.async_block_till_done()
        after_items = entity.todo_items or []
        for item in after_items:
            if item.uid and item.uid not in before:
                return item.uid

        for item in after_items:
            if item.uid and _to_record(item) == record:
                return item.uid
        raise HomeAssistantError(f"Unable to determine created uid for {entity_id}")

    async def async_update(
        self, entity_id: str, uid: str, record: CanonicalTodoItem
    ) -> None:
        """Update one item."""
        entity = self._get_entity(entity_id)
        todo_item = _to_todo_item(record)
        todo_item.uid = uid
        await entity.async_update_todo_item(todo_item)
        await self.hass.async_block_till_done()

    async def async_delete(self, entity_id: str, uid: str) -> None:
        """Delete one item."""
        entity = self._get_entity(entity_id)
        await entity.async_delete_todo_items([uid])
        await self.hass.async_block_till_done()

    async def async_reorder(self, entity_id: str, ordered_uids: list[str]) -> None:
        """Reorder an endpoint to match the requested uid order."""
        entity = self._get_entity(entity_id)
        current = [item.uid for item in entity.todo_items or [] if item.uid is not None]
        if current == ordered_uids:
            return

        for index, uid in enumerate(ordered_uids):
            if uid not in current:
                continue
            if current[index] == uid:
                continue
            previous_uid = ordered_uids[index - 1] if index > 0 else None
            await entity.async_move_todo_item(uid, previous_uid)
            current.remove(uid)
            insert_at = (
                current.index(previous_uid) + 1 if previous_uid in current else 0
            )
            current.insert(insert_at, uid)
        await self.hass.async_block_till_done()
