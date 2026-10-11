# PLAN: Three-way Todo sync (Home Assistant ↔ Google Tasks ↔ iCloud Reminders)

Status: **draft for discussion**. Restart from `main`; PR #1 (`copilot/google-tasks-icloud-reminders-sync`) is reference-only and should not be built upon.

## 1. Goals and scope

- A custom integration (HACS-installable) that keeps **three `todo` entities** in sync: a Home Assistant list, a Google Tasks list, and an iCloud Reminders list. An edit in any of the three propagates to the other two.
- **No sync-direction option.** Always true three-way.
- **Only sync fields the HA `todo` entity model supports** (`TodoItem`): `summary`, `status`, `due` (date or datetime), `description`. Ignore every provider-specific field (priority, subtasks, flags, URLs, locations, repeat rules, alarms/reminders, Google list ordering, etc.).
- **No assumption about CalDAV.** The integration is provider-agnostic: it talks only to the `todo` platform (services/entity state) of whatever three entities the user selects. It must not import, depend on, or mention the `caldav` integration in `manifest.json`.
- Target: latest Home Assistant OS / Core. Use current APIs (`ConfigEntry.runtime_data`, `OptionsFlowWithReload`, typed config entries, `TodoListEntityFeature` enum, `async_forward_entry_setups`).

### Non-goals (v1)
Provider-only fields, subtasks, ordering/move, multiple lists per config entry, more than three endpoints, deletion of whole lists, UI panel.

## 2. Reference code the agent MUST read first

Read the actual source before writing code, and note exactly which `TodoListEntityFeature` flags, UID formats and field quirks each backend exposes.

- Home Assistant `todo` platform (service schema, `TodoItem`, `TodoListEntityFeature`, `todo.get_items` response, `todo.add_item/update_item/remove_item`):
  - https://github.com/home-assistant/core/tree/dev/homeassistant/components/todo
  - https://developers.home-assistant.io/docs/core/entity/todo/
- **Google Tasks** (official): https://github.com/home-assistant/core/tree/dev/homeassistant/components/google_tasks
  - `todo.py`: https://github.com/home-assistant/core/blob/dev/homeassistant/components/google_tasks/todo.py
  - `coordinator.py` (polling cadence) and `api.py`
  - Docs: https://www.home-assistant.io/integrations/google_tasks/
- **Apple iCloud** (official): https://github.com/home-assistant/core/tree/dev/homeassistant/components/icloud
  - `todo.py` (Reminders list entity): https://github.com/home-assistant/core/blob/dev/homeassistant/components/icloud/todo.py
  - `coordinator.py`, `account.py`
  - Docs: https://www.home-assistant.io/integrations/icloud/
- Home Assistant **Local To-do** (likely the HA-side list; simple writable entity): https://github.com/home-assistant/core/tree/dev/homeassistant/components/local_todo
- Custom integration guidance: https://developers.home-assistant.io/docs/creating_integration_manifest/ and https://developers.home-assistant.io/docs/config_entries_config_flow_handler/

### Facts observed in the official code (verify again; they may change)
- Google Tasks `todo.py` features: create, update, delete, move, set due **date**, set description. **Due is date-only** (datetimes truncated to a day); description maps to `notes`. Item UID is the Google task `id`.
- iCloud `todo.py` features: create, update, delete, set due **date and datetime**, set description. Item UID is the reminder `uid`. All-day vs timed is derived from `date` vs `datetime`. Child (sub) reminders are flattened/handled specially.
- Both integrations are **`cloud_polling`**, so changes appear at their poll cadence, not instantly. Sync latency is bounded by that.
- Consequence: the lowest common denominator for `due` is a **date** (see questions).

## 3. Core design

### 3.1 Shape
- Domain: `todo_three_way_sync` (placeholder; confirm name). One config entry = one **sync group** of exactly three `todo` entities: `home_assistant`, `google_tasks`, `icloud`.
- Plain Python sync engine (no HA imports) plus a thin HA adapter, so the engine is unit-testable.
  - `engine.py`: pure three-way merge.
  - `adapter.py`: reads/writes a `todo` entity through HA services (`todo.get_items`, `todo.add_item`, `todo.update_item`, `todo.remove_item`).
  - `store.py`: persistent state (HA `Store`).
  - `manager.py`: triggers, debounce, locking.
  - `config_flow.py`, `__init__.py`, `services.yaml`, `strings.json`, `translations/en.json`, `diagnostics.py`.
- Each endpoint is accessed only through the generic `todo` domain, so no CalDAV/Google/iCloud libraries are imported.

### 3.2 Data model (storage)
HA `Store`, one file `.storage/<domain>`, versioned (with a migration hook from day one). Per config entry:

```
{
  "version": 1,
  "groups": {
    "<entry_id>": {
      "last_sync_at": "...",
      "items": {
        "<canonical_id>": {            // our own UUID
          "ids": { "ha": "...", "google": "...", "icloud": "..." },
          "base": {                    // last value successfully synced (common ancestor)
            "summary": "...", "status": "needs_action|completed",
            "due": "2026-10-11" | "2026-10-11T09:00:00+00:00" | null,
            "description": "..." | null
          },
          "first_seen": "...", "last_synced": "...",
          "deleted_at": null          // tombstone marker
        }
      }
    }
  }
}
```
- `base` is what makes true three-way merge possible.
- Tombstones are kept for a retention period so deletions propagate and do not resurrect.
- Store only what is needed; no provider-specific fields.

### 3.3 Identity mapping
1. Primary key: the per-endpoint item UID stored in `ids`.
2. A new item with no mapping is **linked** only during first-time bootstrap: exact match on normalized summary (+ due when present), and only when unique on every side. Otherwise create counterparts.
3. After linking, **titles are not identity**; renames sync as field edits.
4. If an endpoint UID disappears: treat as delete only if the list read succeeded and is trusted (see safety rules); otherwise skip the cycle.
5. Verify in the iCloud/Google source whether UIDs can change on update, and add a re-link fallback if so.

### 3.4 Merge algorithm (per canonical item, per field)
For each field in {summary, status, due, description}, with current values `h`, `g`, `i` and base `b`:
- All equal: nothing.
- Only one side differs from `b`: that value wins and is written to the other two.
- Several sides changed to the same value: converge, no write on those.
- Real conflict (different new values): deterministic rule (see questions). HA todo items have no per-item modified time, so use a fixed tiebreak (proposal: Home Assistant > iCloud > Google); for `status`, "completed" wins.
- Whole-item rules:
  - New unmapped item on one side: create on the other two (completed ones? see questions).
  - Item gone from one side, unchanged vs `base` elsewhere: delete on the others (tombstone).
  - Item gone on one side but edited on another: **edit wins**, recreate on the missing side.
  - Respect each entity's `supported_features`; skip unsupported fields and log once.
- After a cycle, set `base` to the converged values; save only after writes succeed or are recorded as pending.

### 3.5 Change detection and triggers
- Listen to `state_changed` on the three entities (state is open-item count; a cheap signal) plus a **periodic poll** (default 5 min, configurable) since field-only edits do not change state.
- Debounce (default 5-10 s) and a per-group `asyncio.Lock`; coalesce triggers arriving mid-sync into one follow-up.
- **Echo suppression** without ignore windows: after our writes, `base` is updated, so our own writes produce no diff and no loop.
- Services: `sync_now`, `rebuild_mapping` (reset + bootstrap with no deletes), diagnostics.

### 3.6 Safety rules (deletions)
- If any endpoint is `unavailable`/`unknown` or a fetch fails: **abort the whole cycle**, never treat as empty.
- **Mass-delete guard**: if a cycle would delete more than N items or X% of a list, pause and raise a repair issue; require explicit confirmation.
- First sync (bootstrap) never deletes; it only links and creates.
- Dry-run mode (log only) for first setup.
- Create before delete; update `base` only on success; retry failed writes next cycle.

### 3.7 Config flow (UI only)
- Step 1: name + three `EntitySelector(domain="todo")` fields ("Home Assistant list", "Google Tasks list", "iCloud Reminders list"). Validate distinct, existing, and supporting create/update/delete.
- Do **not** require specific platforms (no CalDAV assumption); optionally warn if the platform is not `google_tasks`/`icloud`/`local_todo`.
- Options: poll interval, debounce, tombstone retention, mass-delete threshold, maybe "sync completed items" and cleanup-after-N-days. **No direction option.**
- Use `OptionsFlowWithReload` and `ConfigEntry.runtime_data`.

## 4. Implementation phases (for the AI agent)

1. **Research (no code).** Read all links in section 2 and summarize: supported features, UID behaviour, due semantics, status values, completion timestamps, poll intervals.
2. **Scaffold.** `custom_components/<domain>/` with manifest (`config_flow: true`, `integration_type: service`, `iot_class: local_polling`, **no** `caldav` anywhere), `hacs.json`, `const.py`, translations; `pyproject.toml` / `requirements_test.txt` with `pytest-homeassistant-custom-component` and `ruff`.
3. **Pure engine + tests first.** `engine.py` with table-driven tests and no HA dependency.
4. **Store.** Versioned `Store` wrapper, migration stub, tombstone GC.
5. **Adapter.** `todo` service wrappers: fetch (normalize date vs datetime, `None` vs empty description), create, update, delete; feature checks via `TodoListEntityFeature`.
6. **Manager.** Triggers, debounce, lock, poll, error handling, events, repair issues.
7. **Config/options flow** and services.
8. **Integration tests** with three fake in-memory `TodoListEntity` (one date-only due like Google, one datetime-capable like iCloud, one plain).
9. **CI** (GitHub Actions: ruff, pytest, hassfest, HACS validation).
10. **README**: install, config, behaviour table (fields × conflict rule), limitations; document only what is tested.

## 5. Test matrix (minimum)

Create/complete/reopen/edit title/edit due/edit description/delete in each of H, G, I, each propagating to the other two. Plus: simultaneous edits to the same field on two sides; different fields on different sides (must merge); delete vs edit; duplicate titles; rename after link; list unavailable (abort); erroneously empty list (mass-delete guard); date-only vs datetime due round-trip without flapping; description `None` vs `""`; restart with persisted state; storage migration; no-loop (second cycle makes zero writes); bootstrap of three pre-populated lists with overlapping titles.

## 6. Known risks / hidden assumptions

- **Due round-trip flapping**: Google drops the time, so datetimes would be rewritten as dates each cycle. Needs a normalization rule.
- **No per-item timestamps** in the `todo` API: conflicts cannot use true last-modified time.
- **Poll latency**: Google and iCloud poll; two quick edits may land in one cycle.
- **Completed items**: some backends hide or limit them; mirroring may be noisy.
- **iCloud subtasks** are flattened/special-cased; confirm no duplicates.
- **Cloud errors**: iCloud 2FA re-auth makes the entity unavailable; the abort-on-unavailable rule covers this.
- **HA-side list**: Local To-do stores in an `.ics` file; check UID stability and `todo.update_item` addressing (uid or summary).

## 7. Questions to narrow scope

1. **HA-side list:** use an existing HA list (Local To-do, Shopping List, ...) or should the integration create/own one? (Existing is simplest.)
2. **Conflicts:** is the tiebreak (HA > iCloud > Google, completed wins) acceptable?
3. **Deletes:** propagate (with mass-delete guard) or "never delete" for v1? Deletion is the riskiest feature.
4. **Completed items:** mirror them (including ones that exist on one side at first sync), or only status changes on linked items? Auto-clean old completed items?
5. **Due times:** treat `due` as **date-only** everywhere (simplest, no flapping), or keep times between HA and iCloud?
6. **Descriptions:** in v1 or deferred?
7. **One group or many:** exactly one group of three lists for v1?
8. **Bootstrap:** on pre-populated lists, link by matching titles, or copy everything after a dry run?
9. **Latency:** is a 5-minute poll plus state-change triggers fine?
10. **Naming:** domain/name preference (placeholder `todo_three_way_sync`; or keep `google_tasks_icloud_reminders_sync`)?
11. **Testing/CI:** OK to add pytest, ruff, hassfest, HACS validation workflows?
12. **HA version floor:** "latest only", or a minimum version for HACS metadata?
13. **Storage:** is `.storage` JSON (HA `Store`) what you meant? Your original message was cut off at "store…": what should be stored besides ID mappings and last-synced base values?

## 8. Definition of done (v1)
- Edits to title, status, due date and description in any of the three lists converge on all three within one poll cycle, without duplicates, flapping or loops.
- No sync-direction option; no provider-specific fields; no `caldav` dependency.
- Deletes (if in scope) are propagated safely; unavailable endpoints never cause deletions.
- Tests pass in CI; README matches actual behaviour.
