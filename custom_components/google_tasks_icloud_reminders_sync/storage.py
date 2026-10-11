"""Storage helpers for the Google Tasks ↔ iCloud Reminders sync integration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION

DEFAULT_PAIR_STATE: dict[str, Any] = {
    "mapping": {
        "google_to_icloud": {},
        "icloud_to_google": {},
    },
    "completion_history": {},
    "last_sync_at": None,
}

DEFAULT_DATA: dict[str, Any] = {"pairs": {}}


class SyncStore:
    """Wrapper around the Home Assistant storage helper."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any]:
        """Load storage data."""
        if self._data is None:
            self._data = await self._store.async_load() or deepcopy(DEFAULT_DATA)
            self._data.setdefault("pairs", {})
        return self._data

    async def async_save(self) -> None:
        """Persist storage data."""
        if self._data is None:
            self._data = deepcopy(DEFAULT_DATA)
        await self._store.async_save(self._data)

    async def async_get_pair_state(self, pair_key: str) -> dict[str, Any]:
        """Return mutable state for one pair."""
        data = await self.async_load()
        pair_state = data["pairs"].setdefault(pair_key, deepcopy(DEFAULT_PAIR_STATE))
        pair_state.setdefault("mapping", {})
        pair_state["mapping"].setdefault("google_to_icloud", {})
        pair_state["mapping"].setdefault("icloud_to_google", {})
        pair_state.setdefault("completion_history", {})
        pair_state.setdefault("last_sync_at", None)
        return pair_state

    async def async_set_pair_state(self, pair_key: str, pair_state: dict[str, Any]) -> None:
        """Replace pair state and persist it."""
        data = await self.async_load()
        data["pairs"][pair_key] = pair_state
        await self.async_save()

    async def async_delete_pair_state(self, pair_key: str) -> None:
        """Delete a pair from storage."""
        data = await self.async_load()
        if pair_key in data["pairs"]:
            del data["pairs"][pair_key]
            await self.async_save()
