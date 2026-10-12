"""Persistent storage for sync state."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.helpers.storage import Store

from .const import (
    CYCLE_LOG_LIMIT,
    DEFAULT_TOMBSTONE_DAYS,
    ENDPOINTS,
    INTEGRATION_VERSION,
    STORAGE_KEY,
    STORAGE_VERSION,
)

type StorageData = dict[str, Any]
type GroupState = dict[str, Any]


def utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def as_iso(value: datetime) -> str:
    """Serialize a timestamp."""
    return value.isoformat()


def make_group_state(
    *,
    settings: dict[str, Any],
    now: datetime | None = None,
) -> GroupState:
    """Create a fresh group state."""
    now = now or utcnow()
    return {
        "schema_version": STORAGE_VERSION,
        "integration_version": INTEGRATION_VERSION,
        "created_at": as_iso(now),
        "settings": deepcopy(settings),
        "items": {},
        "tombstones": {},
        "order": [],
        "endpoint_orders": {endpoint: [] for endpoint in ENDPOINTS},
        "last_sync_at": None,
        "last_sync_result": {},
        "cycle_counter": 0,
        "cycle_log": [],
        "bootstrap_completed": False,
    }


class SyncStore:
    """Wrapper around Home Assistant Store."""

    def __init__(self, hass) -> None:
        """Initialize the store."""
        self._store = Store[StorageData](hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: StorageData = {"groups": {}}

    async def async_load(self) -> None:
        """Load persisted storage."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            self._data = {"groups": {}}
            return
        self._data = {"groups": data.get("groups", {})}

    async def async_save(self) -> None:
        """Persist storage."""
        await self._store.async_save(self._data)

    def get_group(
        self,
        entry_id: str,
        *,
        settings: dict[str, Any],
        now: datetime | None = None,
    ) -> GroupState:
        """Return group state, creating it when missing."""
        groups = self._data.setdefault("groups", {})
        if entry_id not in groups:
            groups[entry_id] = make_group_state(settings=settings, now=now)
        group = groups[entry_id]
        group.setdefault("settings", deepcopy(settings))
        group.setdefault("items", {})
        group.setdefault("tombstones", {})
        group.setdefault("order", [])
        group.setdefault("endpoint_orders", {endpoint: [] for endpoint in ENDPOINTS})
        group.setdefault("cycle_log", [])
        group.setdefault("bootstrap_completed", False)
        return group

    def delete_group(self, entry_id: str) -> None:
        """Delete a group from storage."""
        self._data.get("groups", {}).pop(entry_id, None)

    def replace_group(self, entry_id: str, state: GroupState) -> None:
        """Replace a group state."""
        self._data.setdefault("groups", {})[entry_id] = state

    def purge_expired_tombstones(
        self, state: GroupState, *, now: datetime | None = None
    ) -> int:
        """Delete expired tombstones from a group."""
        now = now or utcnow()
        removed = 0
        for canonical_id, tombstone in list(state["tombstones"].items()):
            expires_at = tombstone.get("expires_at")
            if expires_at and datetime.fromisoformat(expires_at) <= now:
                del state["tombstones"][canonical_id]
                removed += 1
        return removed

    def record_cycle(
        self,
        state: GroupState,
        *,
        result: str,
        creates: int = 0,
        updates: int = 0,
        deletes: int = 0,
        conflicts: int = 0,
        errors: int = 0,
        now: datetime | None = None,
    ) -> None:
        """Append a bounded cycle log entry."""
        now = now or utcnow()
        state["last_sync_at"] = as_iso(now)
        state["last_sync_result"] = {
            "result": result,
            "creates": creates,
            "updates": updates,
            "deletes": deletes,
            "conflicts": conflicts,
            "errors": errors,
        }
        state["cycle_counter"] = int(state.get("cycle_counter", 0)) + 1
        state.setdefault("cycle_log", []).append(
            {
                "cycle_id": state["cycle_counter"],
                "timestamp": as_iso(now),
                "result": result,
                "creates": creates,
                "updates": updates,
                "deletes": deletes,
                "conflicts": conflicts,
                "errors": errors,
            }
        )
        state["cycle_log"] = state["cycle_log"][-CYCLE_LOG_LIMIT:]


def reset_group_state(
    state: GroupState,
    *,
    settings: dict[str, Any],
    now: datetime | None = None,
) -> GroupState:
    """Reset an existing group while keeping metadata shape."""
    now = now or utcnow()
    new_state = make_group_state(settings=settings, now=now)
    new_state["created_at"] = state.get("created_at", as_iso(now))
    return new_state


def tombstone_payload(
    *,
    canonical_id: str,
    ids: dict[str, str | None],
    base: dict[str, Any],
    deleted_in: str | None,
    tombstone_days: int = DEFAULT_TOMBSTONE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create tombstone state."""
    now = now or utcnow()
    return {
        "canonical_id": canonical_id,
        "ids": deepcopy(ids),
        "base": deepcopy(base),
        "deleted_in": deleted_in,
        "deleted_at": as_iso(now),
        "expires_at": as_iso(now + timedelta(days=tombstone_days)),
    }
