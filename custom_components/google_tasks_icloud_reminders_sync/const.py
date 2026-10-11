"""Constants for the Google Tasks ↔ iCloud Reminders sync integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "google_tasks_icloud_reminders_sync"
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = DOMAIN

CONF_CLEANUP_DAYS: Final = "cleanup_days"
CONF_CLEANUP_ENABLED: Final = "cleanup_enabled"
CONF_DEBOUNCE_SECONDS: Final = "debounce_seconds"
CONF_GOOGLE_ENTITY_ID: Final = "google_entity_id"
CONF_ICLOUD_ENTITY_ID: Final = "icloud_entity_id"
CONF_NAME: Final = "name"
CONF_PAIR_ID: Final = "pair_id"
CONF_SYNC_COMPLETION: Final = "sync_completion"
CONF_SYNC_DESCRIPTIONS: Final = "sync_descriptions"
CONF_SYNC_DIRECTION: Final = "sync_direction"
CONF_SYNC_DUE_DATES: Final = "sync_due_dates"

DIRECTION_BOTH: Final = "both"
DIRECTION_GOOGLE_TO_ICLOUD: Final = "google_to_icloud"
DIRECTION_ICLOUD_TO_GOOGLE: Final = "icloud_to_google"
SYNC_DIRECTIONS: Final = (
    DIRECTION_BOTH,
    DIRECTION_GOOGLE_TO_ICLOUD,
    DIRECTION_ICLOUD_TO_GOOGLE,
)

DEFAULT_DEBOUNCE_SECONDS: Final = 5
DEFAULT_SYNC_DIRECTION: Final = DIRECTION_BOTH
DEFAULT_SYNC_COMPLETION: Final = True
DEFAULT_SYNC_DUE_DATES: Final = True
DEFAULT_SYNC_DESCRIPTIONS: Final = False
DEFAULT_CLEANUP_ENABLED: Final = False
DEFAULT_CLEANUP_DAYS: Final = 30

SERVICE_RELOAD: Final = "reload"
SERVICE_RESET_PAIR: Final = "reset_pair"
SERVICE_RESYNC_PAIR: Final = "resync_pair"
SERVICE_SYNC_NOW: Final = "sync_now"

EVENT_ITEM_ADDED: Final = f"{DOMAIN}_item_added"
EVENT_ITEM_COMPLETED: Final = f"{DOMAIN}_item_completed"
EVENT_ITEM_REMOVED: Final = f"{DOMAIN}_item_removed"
EVENT_SYNC_COMPLETED: Final = f"{DOMAIN}_sync_completed"
EVENT_SYNC_FAILED: Final = f"{DOMAIN}_sync_failed"

PAIR_DEFAULTS: Final = {
    CONF_SYNC_DIRECTION: DEFAULT_SYNC_DIRECTION,
    CONF_SYNC_COMPLETION: DEFAULT_SYNC_COMPLETION,
    CONF_SYNC_DUE_DATES: DEFAULT_SYNC_DUE_DATES,
    CONF_SYNC_DESCRIPTIONS: DEFAULT_SYNC_DESCRIPTIONS,
    CONF_CLEANUP_ENABLED: DEFAULT_CLEANUP_ENABLED,
    CONF_CLEANUP_DAYS: DEFAULT_CLEANUP_DAYS,
    CONF_DEBOUNCE_SECONDS: DEFAULT_DEBOUNCE_SECONDS,
}
