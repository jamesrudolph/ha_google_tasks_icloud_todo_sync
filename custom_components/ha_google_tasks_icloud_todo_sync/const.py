"""Constants for the sync integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "ha_google_tasks_icloud_todo_sync"
NAME = "Home Assistant Google Tasks Apple iCloud Todo List Sync"
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1
INTEGRATION_VERSION = "1.0.0"

PLATFORMS: list[Platform] = [Platform.SENSOR]

ENDPOINT_HOME_ASSISTANT = "home_assistant"
ENDPOINT_GOOGLE = "google"
ENDPOINT_ICLOUD = "icloud"
ENDPOINTS = (
    ENDPOINT_HOME_ASSISTANT,
    ENDPOINT_GOOGLE,
    ENDPOINT_ICLOUD,
)

STATUS_NEEDS_ACTION = "needs_action"
STATUS_COMPLETED = "completed"

CONF_GROUP_NAME = "group_name"
CONF_HOME_ASSISTANT_ENTITY_ID = "home_assistant_entity_id"
CONF_GOOGLE_ENTITY_ID = "google_entity_id"
CONF_ICLOUD_ENTITY_ID = "icloud_entity_id"
CONF_TIEBREAK_ORDER = "tiebreak_order"
CONF_POLL_INTERVAL = "poll_interval"
CONF_DEBOUNCE_SECONDS = "debounce_seconds"
CONF_TOMBSTONE_DAYS = "tombstone_days"
CONF_MASS_DELETE_COUNT = "mass_delete_count"
CONF_MASS_DELETE_PERCENT = "mass_delete_percent"
CONF_ORDER_SYNC = "order_sync"
CONF_UNAVAILABLE_GRACE_MINUTES = "unavailable_grace_minutes"

DEFAULT_TIEBREAK_ORDER = [
    ENDPOINT_HOME_ASSISTANT,
    ENDPOINT_ICLOUD,
    ENDPOINT_GOOGLE,
]
DEFAULT_POLL_INTERVAL_MINUTES = 5
DEFAULT_POLL_INTERVAL = timedelta(minutes=DEFAULT_POLL_INTERVAL_MINUTES)
DEFAULT_DEBOUNCE_SECONDS = 5
DEFAULT_TOMBSTONE_DAYS = 30
DEFAULT_MASS_DELETE_COUNT = 10
DEFAULT_MASS_DELETE_PERCENT = 75
DEFAULT_ORDER_SYNC = True
DEFAULT_UNAVAILABLE_GRACE_MINUTES = 60

SERVICE_SYNC_NOW = "sync_now"
SERVICE_REBUILD_MAPPING = "rebuild_mapping"

ATTR_ENTRY_ID = "entry_id"

SYNC_STATUS_IDLE = "idle"
SYNC_STATUS_SYNCING = "syncing"
SYNC_STATUS_OK = "ok"
SYNC_STATUS_ERROR = "error"
SYNC_STATUS_UNAVAILABLE = "unavailable"
SYNC_STATUS_MASS_DELETE_BLOCKED = "mass_delete_blocked"

ISSUE_ID_MASS_DELETE = "mass_delete_guard"
ISSUE_ID_UNAVAILABLE = "endpoint_unavailable"

CYCLE_LOG_LIMIT = 20
CONFLICT_LOG_LIMIT = 20
