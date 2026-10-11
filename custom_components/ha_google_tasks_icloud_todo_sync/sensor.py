"""Diagnostic sensors for sync groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SyncConfigEntry
from .const import DOMAIN


@dataclass(frozen=True, slots=True)
class SyncSensorDescription:
    key: str
    name: str
    device_class: SensorDeviceClass | None = None


SENSORS = (
    SyncSensorDescription("status", "Sync status"),
    SyncSensorDescription(
        "last_sync_at",
        "Last sync time",
        SensorDeviceClass.TIMESTAMP,
    ),
    SyncSensorDescription("pending_count", "Pending sync actions"),
)


async def async_setup_entry(
    hass,
    entry: SyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor entities for one sync group."""
    async_add_entities(
        SyncSensor(entry, description)
        for description in SENSORS
    )


class SyncSensor(SensorEntity):
    """A simple sensor that reflects manager runtime state."""

    _attr_has_entity_name = True

    def __init__(self, entry: SyncConfigEntry, description: SyncSensorDescription) -> None:
        self._entry = entry
        self._description = description
        self._manager = entry.runtime_data.manager
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_class = description.device_class

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._manager.group_name,
            "manufacturer": "Home Assistant",
            "model": "Three-way todo sync group",
        }

    @property
    def native_value(self) -> Any:
        if self._description.key == "status":
            return self._manager.status
        if self._description.key == "last_sync_at":
            return self._manager.last_sync_at
        return self._manager.pending_count

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._manager.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

