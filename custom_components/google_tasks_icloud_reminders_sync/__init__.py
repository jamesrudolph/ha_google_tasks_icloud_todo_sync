"""Home Assistant integration for syncing Google Tasks with iCloud Reminders."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_PAIR_ID,
    DOMAIN,
    SERVICE_RELOAD,
    SERVICE_RESET_PAIR,
    SERVICE_RESYNC_PAIR,
    SERVICE_SYNC_NOW,
)
from .coordinator import PairSyncManager, build_manager_config
from .storage import SyncStore

_LOGGER = logging.getLogger(__name__)

SERVICE_SCHEMA = vol.Schema({vol.Optional(CONF_PAIR_ID): str})


@dataclass(slots=True)
class IntegrationData:
    """Runtime integration state."""

    store: SyncStore
    managers: dict[str, PairSyncManager] = field(default_factory=dict)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = IntegrationData(store=SyncStore(hass))
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one configured sync pair."""
    data: IntegrationData = hass.data[DOMAIN]
    manager = PairSyncManager(
        hass,
        data.store,
        build_manager_config(entry.entry_id, entry.title, dict(entry.data)),
    )
    data.managers[entry.entry_id] = manager
    await manager.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one configured sync pair."""
    data: IntegrationData = hass.data[DOMAIN]
    manager = data.managers.pop(entry.entry_id, None)
    if manager is not None:
        await manager.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove storage for a deleted config entry."""
    data: IntegrationData = hass.data[DOMAIN]
    await data.store.async_delete_pair_state(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SYNC_NOW):
        return

    async def handle_sync_now(call: ServiceCall) -> None:
        for manager in _resolve_managers(hass, call.data.get(CONF_PAIR_ID)):
            await manager.async_sync("service_sync_now")

    async def handle_reset_pair(call: ServiceCall) -> None:
        for manager in _resolve_managers(hass, call.data.get(CONF_PAIR_ID)):
            await manager.async_reset_mapping()

    async def handle_resync_pair(call: ServiceCall) -> None:
        for manager in _resolve_managers(hass, call.data.get(CONF_PAIR_ID)):
            await manager.async_reset_mapping()
            await manager.async_sync("service_resync_pair")

    async def handle_reload(_: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(DOMAIN, SERVICE_SYNC_NOW, handle_sync_now, schema=SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET_PAIR, handle_reset_pair, schema=SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESYNC_PAIR, handle_resync_pair, schema=SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RELOAD, handle_reload)


def _resolve_managers(hass: HomeAssistant, pair_id: str | None) -> list[PairSyncManager]:
    """Resolve zero, one, or many managers from a service call."""
    data: IntegrationData = hass.data[DOMAIN]
    if pair_id is None:
        return list(data.managers.values())

    for entry_id, manager in data.managers.items():
        if pair_id in {entry_id, manager.pair_id}:
            return [manager]
    raise vol.Invalid(f"Unknown pair_id: {pair_id}")
