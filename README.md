# sync-google-tasks-icloud-reminders

Home Assistant custom integration for bidirectionally syncing a Google Tasks `todo` entity with an Apple iCloud Reminders `todo` entity.

## Current scope

This integration is implemented as a HACS custom integration with:

- UI config flow for one sync pair per config entry
- Any number of configured pairs
- Persistent bidirectional UID mapping in Home Assistant storage
- Debounced sync on startup, entity updates, and manual service calls
- Optional sync for completion state, due dates, and descriptions
- Optional cleanup of old completed tasks when completion timestamps are available

## Installation

1. Copy `custom_components/google_tasks_icloud_reminders_sync` into your Home Assistant `custom_components` directory, or install this repository through HACS as a custom repository.
2. Restart Home Assistant.
3. Add **Google Tasks ↔ iCloud Reminders Sync** from **Settings → Devices & services → Add integration**.

## Configuration

Each config entry represents one sync pair.

Required fields:

- Pair name
- Google Tasks `todo` entity
- iCloud Reminders `todo` entity
- Sync direction

Optional behavior:

- Sync completion state
- Sync due dates
- Sync descriptions
- Cleanup completed tasks after `n` days
- Debounce interval in seconds

## Services

The integration registers these services under `google_tasks_icloud_reminders_sync`:

- `sync_now`
- `resync_pair`
- `reset_pair`
- `reload`

`pair_id` accepts either the stored pair ID (slugified name) or the Home Assistant config entry ID.

## Storage

Persistent mapping state is stored with the Home Assistant Storage API under:

- `.storage/google_tasks_icloud_reminders_sync`

Each pair stores:

- `mapping.google_to_icloud`
- `mapping.icloud_to_google`
- `completion_history`
- `last_sync_at`

## Known limitations

- Deletions are only used for optional cleanup; manual deletions are not mirrored to the other side.
- When duplicate task summaries exist on one side, fallback summary matching is skipped for safety.
- Cleanup needs a completion timestamp from the backend or one previously recorded by the sync engine.
- The config flow can validate `todo` entities, but it cannot prove which provider owns a selected entity.

## Development validation

This repository currently has no existing test or lint configuration, so validation is limited to Python syntax checks and JSON verification.
