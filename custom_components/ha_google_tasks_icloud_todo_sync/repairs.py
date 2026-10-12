"""Repair issue helpers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    ISSUE_ID_MASS_DELETE,
    ISSUE_ID_UNAVAILABLE,
)

_LEARN_MORE_URL = "https://github.com/jamesrudolph/sync-google-tasks-icloud-reminders"


def _issue_id(prefix: str, entry_id: str) -> str:
    return f"{prefix}_{entry_id}"


@callback
def async_create_mass_delete_issue(
    hass: HomeAssistant, entry_id: str, details: str
) -> None:
    """Create a persistent mass-delete warning."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_ID_MASS_DELETE, entry_id),
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="mass_delete_guard",
        translation_placeholders={"details": details},
        learn_more_url=_LEARN_MORE_URL,
        data={"entry_id": entry_id},
    )


@callback
def async_delete_mass_delete_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the mass-delete issue."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_ID_MASS_DELETE, entry_id))


@callback
def async_create_unavailable_issue(
    hass: HomeAssistant, entry_id: str, details: str
) -> None:
    """Create a persistent endpoint-unavailable warning."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_ID_UNAVAILABLE, entry_id),
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="endpoint_unavailable",
        translation_placeholders={"details": details},
        learn_more_url=_LEARN_MORE_URL,
        data={"entry_id": entry_id},
    )


@callback
def async_delete_unavailable_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the endpoint-unavailable issue."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_ID_UNAVAILABLE, entry_id))
