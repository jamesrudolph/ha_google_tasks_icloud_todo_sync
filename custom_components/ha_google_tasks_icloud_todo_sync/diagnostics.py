"""Diagnostics support."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import DomainData, SyncConfigEntry
from .const import DOMAIN

_REDACTED_KEYS = {"summary", "description"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("**REDACTED**" if key in _REDACTED_KEYS else _redact(nested))
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(hass, entry: SyncConfigEntry) -> dict[str, Any]:
    """Return entry diagnostics."""
    domain_data: DomainData = hass.data[DOMAIN]
    group = deepcopy(
        domain_data.store.get_group(
            entry.entry_id,
            settings={},
        )
    )
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": entry.data,
            "options": entry.options,
        },
        "manager": entry.runtime_data.manager.diagnostics_data(),
        "storage": _redact(group),
    }

