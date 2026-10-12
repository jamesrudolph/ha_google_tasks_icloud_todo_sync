"""Home Assistant Google Tasks Apple iCloud Todo List Sync."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .adapter import TodoSyncAdapter
from .const import (
    ATTR_ENTRY_ID,
    DOMAIN,
    PLATFORMS,
    SERVICE_REBUILD_MAPPING,
    SERVICE_SYNC_NOW,
)
from .manager import SyncManager
from .storage import SyncStore

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeData:
    """Runtime data stored on a config entry."""

    manager: SyncManager


@dataclass(slots=True)
class DomainData:
    """Shared integration state."""

    store: SyncStore
    adapter: TodoSyncAdapter
    managers: dict[str, SyncManager]


type SyncConfigEntry = ConfigEntry[RuntimeData]


async def async_setup(hass: HomeAssistant, _: dict) -> bool:
    """Set up the integration domain."""
    if DOMAIN not in hass.data:
        store = SyncStore(hass)
        await store.async_load()
        hass.data[DOMAIN] = DomainData(
            store=store,
            adapter=TodoSyncAdapter(hass),
            managers={},
        )
        _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SyncConfigEntry) -> bool:
    """Set up one sync group."""
    domain_data: DomainData = hass.data[DOMAIN]
    manager = SyncManager(hass, entry, domain_data.store, domain_data.adapter)
    entry.runtime_data = RuntimeData(manager=manager)
    domain_data.managers[entry.entry_id] = manager
    await manager.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SyncConfigEntry) -> bool:
    """Unload one sync group."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    domain_data: DomainData = hass.data[DOMAIN]
    manager = domain_data.managers.pop(entry.entry_id)
    await manager.async_unload()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: SyncConfigEntry) -> None:
    """Delete persisted storage when an entry is removed."""
    domain_data: DomainData = hass.data[DOMAIN]
    domain_data.store.delete_group(entry.entry_id)
    await domain_data.store.async_save()


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SYNC_NOW):
        return

    async def _async_invoke(call: ServiceCall, *, rebuild: bool) -> None:
        domain_data: DomainData = hass.data[DOMAIN]
        entry_id = call.data.get(ATTR_ENTRY_ID)
        if entry_id is None:
            managers = list(domain_data.managers.values())
        elif (manager := domain_data.managers.get(entry_id)) is not None:
            managers = [manager]
        else:
            _LOGGER.warning("Unknown sync entry_id for %s: %s", DOMAIN, entry_id)
            return

        for manager in managers:
            if rebuild:
                await manager.async_rebuild_mapping()
            else:
                await manager.async_sync_now()

    async def _async_sync_service(call: ServiceCall) -> None:
        await _async_invoke(call, rebuild=False)

    async def _async_rebuild_service(call: ServiceCall) -> None:
        await _async_invoke(call, rebuild=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_NOW,
        _async_sync_service,
        schema=vol.Schema({vol.Optional(ATTR_ENTRY_ID): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REBUILD_MAPPING,
        _async_rebuild_service,
        schema=vol.Schema({vol.Optional(ATTR_ENTRY_ID): str}),
    )
