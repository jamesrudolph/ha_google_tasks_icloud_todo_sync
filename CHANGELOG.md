# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/), versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-10-11

### Added
- Initial release of the Home Assistant Google Tasks Apple iCloud Todo List Sync custom integration.
- True three-way sync across one Home Assistant list, one Google Tasks list, and one iCloud Reminders list.
- Field-level three-way merge logic for title, completion status, due date, and description.
- Safe delete propagation with tombstones, mass-delete protection, diagnostics, repair issues, and sync status sensors.
- Config flow, options flow, `sync_now` and `rebuild_mapping` services, and a pytest + ruff test suite.
