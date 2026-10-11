# Home Assistant Google Tasks Apple iCloud Todo List Sync

[![GitHub Release][release-badge]][release]
[![License][license-badge]](LICENSE)
[![HACS][hacs-badge]][hacs]
[![Validate][validate-badge]][validate]

A Home Assistant custom integration that keeps **one Google Tasks list**, **one Apple iCloud Reminders list** and **one Home Assistant to-do list** in true three-way sync. Edit an item in any of the three and the change shows up in the other two.

> **Status: pre-release / under development.** The design is in [PLAN.md](PLAN.md). Nothing here is installable yet.

## Features

- True three-way sync between three existing `todo` entities (no sync-direction setting)
- Syncs only fields Home Assistant's `todo` model supports: title, status, due date, description, and item order where the backend supports it
- Deletions propagate; tombstones are kept for 30 days (configurable)
- Configurable tiebreak order for conflicts (default: Home Assistant > iCloud > Google; completed always wins)
- Works with any `todo` entity, with no dependency on the CalDAV integration
- Multiple independent groups: add the integration once per set of three lists
- Per-group sensors, diagnostics (item text redacted by default) and repair issues

## Requirements

- Latest stable Home Assistant (Core or OS)
- The official [Google Tasks](https://www.home-assistant.io/integrations/google_tasks/) and [Apple iCloud](https://www.home-assistant.io/integrations/icloud/) integrations set up, with a to-do list entity for each
- A Home Assistant to-do list (for example [Local To-do](https://www.home-assistant.io/integrations/local_todo/))

## Installation

### HACS (recommended)

1. In HACS, open the menu → **Custom repositories**.
2. Add `https://github.com/jamesrudolph/sync-google-tasks-icloud-reminders` as an **Integration**.
3. Search for **Home Assistant Google Tasks Apple iCloud Todo List Sync**, download it, and restart Home Assistant.

### Manual

1. Download the latest release zip from the [releases page][release].
2. Extract it into `<config>/custom_components/ha_google_tasks_icloud_todo_sync/`.
3. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **Home Assistant Google Tasks Apple iCloud Todo List Sync**.
3. Select the three `todo` entities (Home Assistant, Google Tasks, iCloud) and give the group a name.

Options (per group): tiebreak order, poll interval (default 5 minutes), tombstone retention (default 30 days), mass-delete threshold, order sync on/off.

## How it works

Each item gets a stable internal ID mapped to the item ID in each list. The last synced values are stored as a common ancestor, so changes made in different lists to different fields are merged, and only real conflicts use the tiebreak order. See [PLAN.md](PLAN.md) for details.

## Known limitations

- Google Tasks and iCloud are cloud-polled by Home Assistant, so changes appear after their next refresh.
- Due dates are synced as dates.
- iCloud lists keep their own item order; order-only edits are detected on the poll.
- Provider-specific fields (priority, subtasks, alarms, repeats, etc.) are not synced.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) and [PLAN.md](PLAN.md).

## Releasing

Releases are tag-driven; see [CONTRIBUTING.md](CONTRIBUTING.md#releasing).

## License

TBD (add a `LICENSE` file before the first release).

[release-badge]: https://img.shields.io/github/v/release/jamesrudolph/sync-google-tasks-icloud-reminders
[release]: https://github.com/jamesrudolph/sync-google-tasks-icloud-reminders/releases
[license-badge]: https://img.shields.io/github/license/jamesrudolph/sync-google-tasks-icloud-reminders
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs]: https://hacs.xyz
[validate-badge]: https://github.com/jamesrudolph/sync-google-tasks-icloud-reminders/actions/workflows/validate.yml/badge.svg
[validate]: https://github.com/jamesrudolph/sync-google-tasks-icloud-reminders/actions/workflows/validate.yml
