# PLAN: Three-way Todo sync (Home Assistant ↔ Google Tasks ↔ iCloud Reminders)

Status: **draft for discussion**. Restart from `main`; PR #1 (`copilot/google-tasks-icloud-reminders-sync`) is reference-only and should not be built upon.

## 1. Goals and scope

- A custom integration (HACS-installable) that keeps **three `todo` entities** in sync: a Home Assistant list, a Google Tasks list, and an iCloud Reminders list. An edit in any of the three propagates to the other two.
- **No sync-direction option.** Always true three-way.
- **Only sync fields the HA `todo` entity model supports** (`TodoItem`): `summary`, `status`, `due` (date or datetime), `description`. Ignore every provider-specific field (priority, subtasks, flags, URLs, locations, repeat rules, alarms/reminders, Google list ordering, etc.).
- **No assumption about CalDAV.** The integration is provider-agnostic: it talks only to the `todo` platform (services/entity state) of whatever three entities the user selects. It must not import, depend on, or mention the `caldav` integration in `manifest.json`.
- Target: latest Home Assistant OS / Core. Use current APIs (`ConfigEntry.runtime_data`, `OptionsFlowWithReload`, typed config entries, `TodoListEntityFeature` enum, `async_forward_entry_setups`).

### Non-goals (v1)
Provider-only fields, subtasks, more than three lists per group (multiple groups = multiple config entries), more than three endpoints, deletion of whole lists, UI panel.

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
- Real conflict (different new values): deterministic rule (see questions). HA todo items have no per-item modified time, so use a fixed tiebreak (default: Home Assistant > iCloud > Google, user-configurable); for `status`, "completed" wins.
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
- Options: tiebreak order, poll interval, debounce, tombstone retention (default 30 days), mass-delete threshold. **No direction option** and no completed-item cleanup.
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

## 7. Decisions (from user answers) and remaining questions

### Decisions
1. **Entities pre-exist.** All three `todo` entities already exist; the config flow only selects them. The integration never creates lists.
2. **Tiebreak order is user-configurable.** Default Home Assistant > iCloud > Google; "completed wins" stays fixed for `status`. See 7.1 for the drag/drop question.
3. **Deletions sync in v1** with a **30-day tombstone retention** (configurable) in the JSON Store, plus the mass-delete guard (3.6).
4. **Lists are fully in sync.** A fresh/forced rebuild resolves field conflicts with the tiebreak order. **Old completed items are retained** and never auto-cleaned; items are removed only when a user explicitly deletes them in one of the lists. (Drop the "cleanup completed after N days" option.) Completed items are mirrored like any other item.
5. **`due` is a date everywhere** (v1). Store it internally as an ISO date (or datetime if a backend supplies one, for fidelity), but compare by date and push a plain date to Google (and to the others). This removes the flapping risk in section 6.
6. **Descriptions are in v1.**
7. **Multiple sync groups:** each config entry is one group of three lists; the user may add the integration multiple times (one entry per group). Storage is keyed by `entry_id` (already designed in 3.2). An entity may belong to only one group; the config flow rejects overlaps.
8. **Bootstrap / forced rebuild:** match items by normalized title; resolve description/due via the tiebreak order. **Duplicate titles within a list are separate items**: match them one-to-one (pair the Nth duplicate on each side in a stable order, e.g. by due date then list order), never collapse them, and create counterparts for any surplus.
9. **Poll every 5 minutes** plus state-change triggers.

### 7.1 Round-2 decisions
- **Tiebreak UI:** three priority dropdowns ("1st / 2nd / 3rd"), each endpoint used once. Drag/drop is deferred past v1.
- **Item order is synced where possible** (see 7.2).
- **Edit wins over delete** when both happen between two syncs (item is recreated on the side that deleted it).
- **Forced rebuild never deletes**; it only links, creates and updates.
- **Duplicate titles:** see 7.3 (pairing rule explained).
- **CI/tests:** pytest, ruff, hassfest and HACS validation workflows are approved.
- **Versions:** target the latest stable Home Assistant release as of October 2026 (expected 2026.10.x; the agent must confirm the exact current stable at https://www.home-assistant.io/blog/categories/release-notes/ and https://github.com/home-assistant/core/releases), the latest stable Python that release requires, and latest stable `pytest-homeassistant-custom-component`, `ruff` and GitHub Actions versions. Set the minimum HA version in `hacs.json` to that release. Do not support older versions.
- **Per-group options:** yes (poll interval, tombstone days, mass-delete threshold, tiebreak order).
- **Storage:** store as much as helps reconciliation and debugging (see 7.4).

### 7.2 Syncing item order
- `todo.get_items` returns items in each list's own order, so the order can be read from all three.
- Reordering is a separate capability, `TodoListEntityFeature.MOVE_TODO_ITEM`. From the official code, Google Tasks supports it (`async_move_todo_item`, calls the API `move` with `previous`); the iCloud `todo.py` feature list does **not** include it. Verify against current `dev` source, and check the HA-side entity too (Local To-do supports move in current core; confirm).
- In core, move is exposed through the websocket command `todo/item/move`, not a normal service. The adapter must call it through the websocket handler/entity method (verify how to invoke from a custom integration: calling `entity.async_move_todo_item` via the entity component is the likely route).
- Rule: the **canonical order** is the order of the highest-priority endpoint (per the tiebreak list) whose order changed since the last sync; if none changed, keep the stored order. Apply it only to endpoints that support move; others are left as they are (their order is not changed, but items are still synced).
- Store `order: [canonical_id, ...]` per group, and per-endpoint last-seen order, so order changes can be detected as a change (like any other field).
- Risks: order-only edits do not change entity state, so they are only noticed on the 5-minute poll; new items land at the end of the other lists and are then moved; iCloud order may be unmovable, so Home Assistant/Google may end up ordered differently from iCloud. Document this clearly.
- Make order sync an option (default on) so it can be disabled if it causes churn. Treat it as a late implementation phase, after core field sync is solid.

### 7.3 Duplicate titles: what I meant (CONFIRMED by user: Nth-duplicate pairing)
On first run or forced rebuild we link items across lists by title. If a list holds two items called "Milk", we cannot know which "Milk" on list A is which on list B. Your rule: they are separate items. To link them anyway, pair them one-to-one: the first "Milk" on each side, the second "Milk" on each side, and so on, using a stable ordering (list order, then due date). The pairing may be "wrong" in that the first Milk in Google might have been meant as the second in iCloud, but since both are called Milk the only visible effect is that description/due date follow the tiebreak winner for that pair. Extra duplicates (3 on one side, 2 on another) get a new counterpart created for the surplus. After linking, identity is by stored IDs, so it never re-pairs.

### 7.4 Storage: more for reconciliation and debugging
Add per group (in addition to section 3.2):
- `schema_version`, integration version, and `created_at`.
- Settings snapshot: entity IDs, tiebreak order, retention days.
- Per item: `ids` per endpoint, `base` values, `base_hash`, per-endpoint last-seen values/hashes, `last_changed_by`, `last_changed_at`, `first_seen_on`, `link_method` (`uid`, `title_match`, `created`), `pending` write queue with retry counts, `conflicts` history (last N: field, winner, losers, cycle id).
- Tombstones: canonical id, ids, last base values (so a delete-vs-edit decision and a restore are possible), `deleted_in`, `deleted_at`, `expires_at` (30 days).
- Group-level: `order`, per-endpoint last-seen order, `last_sync_at`, `last_sync_result`, cycle counter, rolling log of the last N cycles (counts of creates/updates/deletes/conflicts/errors), bootstrap/rebuild history.
- Expose all of it through the diagnostics platform (redacting nothing sensitive beyond entity-ish identifiers; task text may be personal, so redact summaries/descriptions in diagnostics by default).
- Keep the file bounded (cap logs, GC expired tombstones).

### 7.5 Naming ideas
The tool links exactly one Google Tasks list, one iCloud Reminders list and one Home Assistant list. Ideas, with a suggested domain:
- **Tri-List Sync** (`tri_list_sync`)
- **Triple Todo Sync** (`triple_todo_sync`)
- **Todo Triangle** (`todo_triangle`)
- **ListLink** (`listlink`) / **Todo Link** (`todo_link`)
- **Linked Todo Lists** (`linked_todo_lists`)
- **Three-Way Todo Sync** (`three_way_todo_sync`): the most descriptive, my pick
- Keep `google_tasks_icloud_reminders_sync`: ties the name to two providers and excludes the HA side, so less good.
Domain names are permanent once users install, so choose before coding. HA domains must be lowercase snake_case and unique (check it does not clash with an existing core integration).

### 7.6 Remaining questions
1. Pick a name (7.5).
2. Order sync (7.2): OK that iCloud (and any endpoint without move support) keeps its own order, and that order-only edits are detected only on the 5-minute poll?
3. ~~Duplicate pairing (7.3)~~: confirmed, Nth-duplicate pairing.
4. Diagnostics (7.4): is redacting item text by default fine?
5. Do you want a Home Assistant **sensor/binary_sensor** per group (last sync time, status, pending conflicts) in v1, or only services/events and diagnostics?
6. Do you want persistent notifications / repair issues when the mass-delete guard trips or an endpoint is unavailable for a long time?

## 8. Definition of done (v1)
- Edits to title, status, due date and description in any of the three lists converge on all three within one poll cycle, without duplicates, flapping or loops.
- No sync-direction option; no provider-specific fields; no `caldav` dependency.
- Deletes (if in scope) are propagated safely; unavailable endpoints never cause deletions.
- Tests pass in CI; README matches actual behaviour.
