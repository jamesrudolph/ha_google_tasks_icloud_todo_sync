"""Sync engine for the Google Tasks ↔ iCloud Reminders sync integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLEANUP_DAYS,
    CONF_CLEANUP_ENABLED,
    CONF_DEBOUNCE_SECONDS,
    CONF_GOOGLE_ENTITY_ID,
    CONF_ICLOUD_ENTITY_ID,
    CONF_NAME,
    CONF_PAIR_ID,
    CONF_SYNC_COMPLETION,
    CONF_SYNC_DESCRIPTIONS,
    CONF_SYNC_DIRECTION,
    CONF_SYNC_DUE_DATES,
    DEFAULT_DEBOUNCE_SECONDS,
    DIRECTION_BOTH,
    DIRECTION_GOOGLE_TO_ICLOUD,
    DIRECTION_ICLOUD_TO_GOOGLE,
    EVENT_ITEM_ADDED,
    EVENT_ITEM_COMPLETED,
    EVENT_ITEM_REMOVED,
    EVENT_SYNC_COMPLETED,
    EVENT_SYNC_FAILED,
)
from .storage import SyncStore

_LOGGER = logging.getLogger(__name__)

TODO_DOMAIN = "todo"
SERVICE_ADD_ITEM = "add_item"
SERVICE_GET_ITEMS = "get_items"
SERVICE_REMOVE_ITEM = "remove_item"
SERVICE_UPDATE_ITEM = "update_item"
STATUS_COMPLETED = "completed"
STATUS_NEEDS_ACTION = "needs_action"
FEATURE_SET_DUE_DATE = 16
FEATURE_SET_DUE_DATETIME = 32
FEATURE_SET_DESCRIPTION = 64
FEATURE_DELETE = 2
POLL_INTERVAL = timedelta(minutes=5)


@dataclass(slots=True)
class NormalizedTask:
    """Canonical task representation used by the sync engine."""

    uid: str | None
    summary: str
    status: str
    due: date | datetime | None
    description: str | None
    completed: datetime | None
    source: str
    entity_id: str

    @property
    def canonical_summary(self) -> str:
        """Return normalized summary for fallback matching."""
        return canonicalize_summary(self.summary)


class PairSyncManager:
    """Manage syncing for one configured pair."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: SyncStore,
        config: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.store = store
        self.config = config
        self._remove_listener = None
        self._scheduled_unsub = None
        self._poll_unsub = None
        self._tasks: set[asyncio.Task] = set()
        self._stopped = False
        self._suppress_trailing = False
        self._syncing = False
        self._pending_sync = False
        self._ignore_changes_until = 0.0
        self._warned_missing_cleanup_timestamps = False

    @property
    def entry_id(self) -> str:
        return self.config["entry_id"]

    @property
    def pair_id(self) -> str:
        return self.config[CONF_PAIR_ID]

    @property
    def name(self) -> str:
        return self.config[CONF_NAME]

    @property
    def google_entity_id(self) -> str:
        return self.config[CONF_GOOGLE_ENTITY_ID]

    @property
    def icloud_entity_id(self) -> str:
        return self.config[CONF_ICLOUD_ENTITY_ID]

    async def async_start(self) -> None:
        """Start listening for state changes."""
        self._remove_listener = async_track_state_change_event(
            self.hass,
            [self.google_entity_id, self.icloud_entity_id],
            self._async_handle_state_change,
        )
        if self.config[CONF_SYNC_DUE_DATES] or self.config[CONF_SYNC_DESCRIPTIONS]:
            # Todo entity state changes do not expose due-date or description edits.
            self._poll_unsub = async_track_time_interval(
                self.hass, self._async_handle_poll, POLL_INTERVAL
            )
        await self.async_schedule_sync("startup", immediate=True)

    async def async_stop(self) -> None:
        """Stop listeners, pending work, and any running sync."""
        self._stopped = True
        if self._scheduled_unsub is not None:
            self._scheduled_unsub()
            self._scheduled_unsub = None
        if self._poll_unsub is not None:
            self._poll_unsub()
            self._poll_unsub = None
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @callback
    def _async_start_sync_task(self, reason: str) -> None:
        """Run a tracked sync task so it can be cancelled on stop."""
        if self._stopped:
            return
        task = self.hass.async_create_task(self.async_sync(reason))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _async_handle_poll(self, _: Any) -> None:
        """Periodically sync to pick up metadata-only edits."""
        self._async_start_sync_task("poll")

    @callback
    def _async_handle_state_change(self, event: Event) -> None:
        """Debounce sync work for source entity changes."""
        in_ignore_window = dt_util.utcnow().timestamp() < self._ignore_changes_until
        if in_ignore_window and self._suppress_trailing:
            return
        reason = "trailing" if in_ignore_window else "state_changed"
        self.hass.async_create_task(self.async_schedule_sync(reason))

    async def async_schedule_sync(self, reason: str, immediate: bool = False) -> None:
        """Schedule a sync for this pair."""
        if self._stopped:
            return
        if self._scheduled_unsub is not None:
            self._scheduled_unsub()
            self._scheduled_unsub = None

        delay = 0 if immediate else max(0, int(self.config.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS)))

        @callback
        def _run(_: Any) -> None:
            self._scheduled_unsub = None
            self._async_start_sync_task(reason)

        self._scheduled_unsub = async_call_later(self.hass, delay, _run)

    async def async_reset_mapping(self) -> None:
        """Clear persisted mappings for this pair."""
        pair_state = await self.store.async_get_pair_state(self.entry_id)
        pair_state["mapping"] = {"google_to_icloud": {}, "icloud_to_google": {}}
        pair_state["completion_history"] = {}
        pair_state["status_snapshot"] = {}
        await self.store.async_set_pair_state(self.entry_id, pair_state)

    async def async_sync(self, reason: str = "manual") -> None:
        """Run one sync pass."""
        if self._syncing:
            self._pending_sync = True
            return

        self._syncing = True
        self._suppress_trailing = reason == "trailing"
        self._ignore_changes_until = dt_util.utcnow().timestamp() + max(
            1, int(self.config.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS))
        )

        stats = {"items_added": 0, "items_completed": 0, "items_removed": 0}
        try:
            pair_state = await self.store.async_get_pair_state(self.entry_id)
            google_tasks = await self._async_fetch_tasks(self.google_entity_id, "google")
            icloud_tasks = await self._async_fetch_tasks(self.icloud_entity_id, "icloud")

            self._reconcile_mappings(pair_state, google_tasks, icloud_tasks)

            direction = self.config[CONF_SYNC_DIRECTION]
            if direction in (DIRECTION_BOTH, DIRECTION_GOOGLE_TO_ICLOUD):
                added = await self._async_add_missing_tasks(
                    pair_state,
                    google_tasks,
                    icloud_tasks,
                    "google",
                    "icloud",
                )
                stats["items_added"] += added
                if added:
                    icloud_tasks = await self._async_fetch_tasks(self.icloud_entity_id, "icloud")
                    self._reconcile_mappings(pair_state, google_tasks, icloud_tasks)

            if direction in (DIRECTION_BOTH, DIRECTION_ICLOUD_TO_GOOGLE):
                added = await self._async_add_missing_tasks(
                    pair_state,
                    icloud_tasks,
                    google_tasks,
                    "icloud",
                    "google",
                )
                stats["items_added"] += added
                if added:
                    google_tasks = await self._async_fetch_tasks(self.google_entity_id, "google")
                    self._reconcile_mappings(pair_state, google_tasks, icloud_tasks)

            if self.config[CONF_SYNC_COMPLETION]:
                stats["items_completed"] += await self._async_sync_completion(
                    pair_state, google_tasks, icloud_tasks, direction
                )

            for enabled, sync_due, sync_description in (
                (self.config[CONF_SYNC_DUE_DATES], True, False),
                (self.config[CONF_SYNC_DESCRIPTIONS], False, True),
            ):
                if not enabled:
                    continue
                updated_uids: set[str] = set()
                if direction in (DIRECTION_BOTH, DIRECTION_GOOGLE_TO_ICLOUD):
                    updated_uids = await self._async_sync_metadata(
                        pair_state,
                        google_tasks,
                        icloud_tasks,
                        "google",
                        "icloud",
                        sync_due=sync_due,
                        sync_description=sync_description,
                        allow_clear=direction != DIRECTION_BOTH,
                        skip_source_uids=set(),
                    )
                if direction in (DIRECTION_BOTH, DIRECTION_ICLOUD_TO_GOOGLE):
                    # Google wins conflicts in bidirectional mode: skip iCloud tasks just updated.
                    await self._async_sync_metadata(
                        pair_state,
                        icloud_tasks,
                        google_tasks,
                        "icloud",
                        "google",
                        sync_due=sync_due,
                        sync_description=sync_description,
                        allow_clear=direction != DIRECTION_BOTH,
                        skip_source_uids=updated_uids,
                    )

            stats["items_removed"] += await self._async_cleanup(pair_state, google_tasks)
            stats["items_removed"] += await self._async_cleanup(pair_state, icloud_tasks)

            pair_state["last_sync_at"] = dt_util.utcnow().isoformat()
            await self.store.async_set_pair_state(self.entry_id, pair_state)
            self.hass.bus.async_fire(
                EVENT_SYNC_COMPLETED,
                {
                    "pair_id": self.pair_id,
                    "name": self.name,
                    "google_entity_id": self.google_entity_id,
                    "icloud_entity_id": self.icloud_entity_id,
                    "reason": reason,
                    **stats,
                },
            )
        except Exception as err:
            _LOGGER.exception("Failed syncing pair %s", self.pair_id)
            self.hass.bus.async_fire(
                EVENT_SYNC_FAILED,
                {
                    "pair_id": self.pair_id,
                    "name": self.name,
                    "google_entity_id": self.google_entity_id,
                    "icloud_entity_id": self.icloud_entity_id,
                    "reason": reason,
                    "error": str(err),
                },
            )
        finally:
            self._syncing = False
            self._ignore_changes_until = dt_util.utcnow().timestamp() + max(
                1, int(self.config.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS))
            )
            if self._pending_sync and not self._stopped:
                self._pending_sync = False
                await self.async_schedule_sync("pending")

    async def _async_fetch_tasks(self, entity_id: str, source: str) -> list[NormalizedTask]:
        """Fetch items for one todo entity."""
        state = self.hass.states.get(entity_id)
        if state is None:
            raise HomeAssistantError(f"Entity not found: {entity_id}")
        if state.state in {"unavailable", "unknown"}:
            raise HomeAssistantError(f"Entity unavailable: {entity_id}")

        result = await self.hass.services.async_call(
            TODO_DOMAIN,
            SERVICE_GET_ITEMS,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
            return_response=True,
        )
        if not result or entity_id not in result:
            return []

        return [
            self._normalize_task(item, source, entity_id)
            for item in result[entity_id].get("items", [])
        ]

    def _normalize_task(
        self, item: dict[str, Any], source: str, entity_id: str
    ) -> NormalizedTask:
        """Normalize raw todo item data."""
        due = _parse_due(item.get("due"))
        completed = dt_util.parse_datetime(item.get("completed")) if item.get("completed") else None
        return NormalizedTask(
            uid=item.get("uid"),
            summary=(item.get("summary") or "").strip(),
            status=item.get("status") or STATUS_NEEDS_ACTION,
            due=due,
            description=(item.get("description") or "").strip() or None,
            completed=completed,
            source=source,
            entity_id=entity_id,
        )

    def _reconcile_mappings(
        self,
        pair_state: dict[str, Any],
        google_tasks: list[NormalizedTask],
        icloud_tasks: list[NormalizedTask],
    ) -> None:
        """Repair mappings by summary fallback, only for summaries unique on both sides."""
        google_index = _build_summary_index(google_tasks)
        icloud_index = _build_summary_index(icloud_tasks)
        for google_task in google_tasks:
            if not google_task.uid:
                continue
            if pair_state["mapping"]["google_to_icloud"].get(google_task.uid):
                continue
            if len(google_index.get(google_task.canonical_summary, [])) != 1:
                continue
            match = _unique_summary_match(icloud_index, google_task.canonical_summary)
            if match and match.uid:
                self._store_mapping(pair_state, google_task.uid, match.uid)

        for icloud_task in icloud_tasks:
            if not icloud_task.uid:
                continue
            if pair_state["mapping"]["icloud_to_google"].get(icloud_task.uid):
                continue
            if len(icloud_index.get(icloud_task.canonical_summary, [])) != 1:
                continue
            match = _unique_summary_match(google_index, icloud_task.canonical_summary)
            if match and match.uid:
                self._store_mapping(pair_state, match.uid, icloud_task.uid)

    async def _async_add_missing_tasks(
        self,
        pair_state: dict[str, Any],
        source_tasks: list[NormalizedTask],
        destination_tasks: list[NormalizedTask],
        source_name: str,
        destination_name: str,
    ) -> int:
        """Create missing open tasks on the destination list."""
        added = 0
        destination_index = _build_summary_index(destination_tasks)

        for source_task in source_tasks:
            if source_task.status != STATUS_NEEDS_ACTION or not source_task.summary:
                continue
            if self._find_mapped_task(
                pair_state, source_task, source_tasks, destination_tasks, source_name
            ):
                continue
            if destination_index.get(source_task.canonical_summary):
                # Ambiguous matches cannot be mapped; creating more would duplicate forever.
                continue

            try:
                await self._async_add_task(destination_name, source_task)
            except Exception:
                _LOGGER.exception(
                    "Failed to add task '%s' for pair %s", source_task.summary, self.pair_id
                )
                continue

            added += 1
            self.hass.bus.async_fire(
                EVENT_ITEM_ADDED,
                {
                    "pair_id": self.pair_id,
                    "name": self.name,
                    "source": source_name,
                    "destination": destination_name,
                    "summary": source_task.summary,
                },
            )
        return added

    async def _async_add_task(self, destination_name: str, task: NormalizedTask) -> None:
        """Create one task on a destination list."""
        entity_id = self._entity_id_for_source(destination_name)
        data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id, "item": task.summary}
        state = self.hass.states.get(entity_id)
        if state is None:
            raise HomeAssistantError(f"Entity not found: {entity_id}")

        supported_features = int(state.attributes.get("supported_features", 0))
        if self.config[CONF_SYNC_DUE_DATES] and task.due is not None:
            self._apply_due_to_service_data(data, supported_features, task.due)
        if (
            self.config[CONF_SYNC_DESCRIPTIONS]
            and task.description
            and supported_features & FEATURE_SET_DESCRIPTION
        ):
            data["description"] = task.description

        await self.hass.services.async_call(
            TODO_DOMAIN,
            SERVICE_ADD_ITEM,
            data,
            blocking=True,
        )

    async def _async_sync_completion(
        self,
        pair_state: dict[str, Any],
        google_tasks: list[NormalizedTask],
        icloud_tasks: list[NormalizedTask],
        direction: str,
    ) -> int:
        """Propagate completed and reopened status for matched tasks.

        In bidirectional mode the side that changed since the last synced
        status wins; if both changed (or nothing is known) completed wins.
        """
        completed_count = 0
        snapshot = pair_state["status_snapshot"]
        for google_task in google_tasks:
            if not google_task.uid:
                continue
            icloud_task = self._find_mapped_task(
                pair_state, google_task, google_tasks, icloud_tasks, "google"
            )
            if not icloud_task:
                continue
            if google_task.status == icloud_task.status:
                snapshot[google_task.uid] = google_task.status
                continue

            if direction == DIRECTION_GOOGLE_TO_ICLOUD:
                google_wins = True
            elif direction == DIRECTION_ICLOUD_TO_GOOGLE:
                google_wins = False
            else:
                last_status = snapshot.get(google_task.uid)
                if last_status == icloud_task.status:
                    google_wins = True
                elif last_status == google_task.status:
                    google_wins = False
                else:
                    google_wins = google_task.status == STATUS_COMPLETED

            if google_wins:
                source_task, destination_task = google_task, icloud_task
                source_name, destination_name = "google", "icloud"
            else:
                source_task, destination_task = icloud_task, google_task
                source_name, destination_name = "icloud", "google"

            try:
                await self._async_update_task(
                    destination_name,
                    destination_task,
                    {"status": source_task.status},
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to update status of task '%s' for pair %s",
                    source_task.summary,
                    self.pair_id,
                )
                continue

            snapshot[google_task.uid] = source_task.status
            if source_task.status != STATUS_COMPLETED:
                for task in (source_task, destination_task):
                    pair_state["completion_history"].pop(
                        f"{task.source}:{task.uid or task.summary}", None
                    )
                continue

            self._record_completion(pair_state, source_task)
            self._record_completion(pair_state, destination_task)
            completed_count += 1
            self.hass.bus.async_fire(
                EVENT_ITEM_COMPLETED,
                {
                    "pair_id": self.pair_id,
                    "name": self.name,
                    "source": source_name,
                    "destination": destination_name,
                    "summary": source_task.summary,
                },
            )
        return completed_count

    async def _async_sync_metadata(
        self,
        pair_state: dict[str, Any],
        source_tasks: list[NormalizedTask],
        destination_tasks: list[NormalizedTask],
        source_name: str,
        destination_name: str,
        *,
        sync_due: bool,
        sync_description: bool,
        allow_clear: bool,
        skip_source_uids: set[str],
    ) -> set[str]:
        """Copy due dates or descriptions when different; return updated destination uids.

        Cleared source values only propagate when allow_clear is set (one-way
        sync), so bidirectional sync never wipes a value set on the other side.
        """
        updated_uids: set[str] = set()
        destination_entity_id = self._entity_id_for_source(destination_name)
        destination_state = self.hass.states.get(destination_entity_id)
        if destination_state is None:
            return updated_uids
        supported_features = int(destination_state.attributes.get("supported_features", 0))

        for source_task in source_tasks:
            if source_task.uid and source_task.uid in skip_source_uids:
                continue
            destination_task = self._find_mapped_task(
                pair_state, source_task, source_tasks, destination_tasks, source_name
            )
            if not destination_task:
                continue

            update: dict[str, Any] = {}
            if (
                sync_due
                and (source_task.due is not None or allow_clear)
                and not _same_due(source_task.due, destination_task.due)
            ):
                self._apply_due_to_service_data(update, supported_features, source_task.due)
            if (
                sync_description
                and (source_task.description or allow_clear)
                and source_task.description != destination_task.description
                and supported_features & FEATURE_SET_DESCRIPTION
            ):
                update["description"] = source_task.description
            if not update:
                continue

            try:
                await self._async_update_task(destination_name, destination_task, update)
            except Exception:
                _LOGGER.exception(
                    "Failed to update metadata for '%s' on pair %s",
                    source_task.summary,
                    self.pair_id,
                )
                continue
            if destination_task.uid:
                updated_uids.add(destination_task.uid)
        return updated_uids

    async def _async_cleanup(
        self, pair_state: dict[str, Any], tasks: list[NormalizedTask]
    ) -> int:
        """Delete old completed tasks when configured."""
        if not self.config[CONF_CLEANUP_ENABLED] or self.config[CONF_CLEANUP_DAYS] <= 0:
            return 0

        removed = 0
        cutoff = dt_util.utcnow() - timedelta(days=int(self.config[CONF_CLEANUP_DAYS]))
        entity_state = self.hass.states.get(tasks[0].entity_id) if tasks else None
        if entity_state is None or not int(entity_state.attributes.get("supported_features", 0)) & FEATURE_DELETE:
            return 0
        for task in tasks:
            if task.status != STATUS_COMPLETED:
                continue

            completed_at = task.completed or self._lookup_completion(pair_state, task)
            if completed_at is None:
                if not self._warned_missing_cleanup_timestamps:
                    _LOGGER.warning(
                        "Skipping cleanup for pair %s because completed timestamps are unavailable for some items",
                        self.pair_id,
                    )
                    self._warned_missing_cleanup_timestamps = True
                continue
            if completed_at > cutoff:
                continue

            try:
                await self.hass.services.async_call(
                    TODO_DOMAIN,
                    SERVICE_REMOVE_ITEM,
                    {ATTR_ENTITY_ID: task.entity_id, "item": [task.uid or task.summary]},
                    blocking=True,
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to remove completed task '%s' for pair %s",
                    task.summary,
                    self.pair_id,
                )
                continue

            removed += 1
            self._remove_task_from_mapping(pair_state, task)
            self.hass.bus.async_fire(
                EVENT_ITEM_REMOVED,
                {
                    "pair_id": self.pair_id,
                    "name": self.name,
                    "entity_id": task.entity_id,
                    "summary": task.summary,
                },
            )
        return removed

    async def _async_update_task(
        self, destination_name: str, task: NormalizedTask, update: dict[str, Any]
    ) -> None:
        """Apply an update to an existing destination task."""
        entity_id = self._entity_id_for_source(destination_name)
        data = {ATTR_ENTITY_ID: entity_id, "item": task.uid or task.summary, **update}
        await self.hass.services.async_call(
            TODO_DOMAIN,
            SERVICE_UPDATE_ITEM,
            data,
            blocking=True,
        )

    def _apply_due_to_service_data(
        self, data: dict[str, Any], supported_features: int, due: date | datetime | None
    ) -> None:
        """Map normalized due values to supported service fields."""
        if due is None:
            if supported_features & FEATURE_SET_DUE_DATE:
                data["due_date"] = None
            elif supported_features & FEATURE_SET_DUE_DATETIME:
                data["due_datetime"] = None
            return
        if isinstance(due, datetime):
            if supported_features & FEATURE_SET_DUE_DATETIME:
                data["due_datetime"] = due
                return
            if supported_features & FEATURE_SET_DUE_DATE:
                data["due_date"] = due.date()
                return
        if isinstance(due, date) and supported_features & FEATURE_SET_DUE_DATE:
            data["due_date"] = due
            return
        if isinstance(due, date) and supported_features & FEATURE_SET_DUE_DATETIME:
            data["due_datetime"] = dt_util.as_local(
                datetime.combine(due, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
            )

    def _find_mapped_task(
        self,
        pair_state: dict[str, Any],
        source_task: NormalizedTask,
        source_tasks: list[NormalizedTask],
        destination_tasks: list[NormalizedTask],
        source_name: str,
    ) -> NormalizedTask | None:
        """Find a destination task using mappings then summary fallback.

        The fallback requires the summary to be unique on both sides.
        """
        destination_uid = None
        if source_task.uid:
            mapping_key = "google_to_icloud" if source_name == "google" else "icloud_to_google"
            destination_uid = pair_state["mapping"][mapping_key].get(source_task.uid)
        if destination_uid:
            for task in destination_tasks:
                if task.uid == destination_uid:
                    return task
            if source_task.uid:
                self._remove_stale_mapping(pair_state, source_name, source_task.uid)
        canonical_summary = source_task.canonical_summary
        if len(_build_summary_index(source_tasks).get(canonical_summary, [])) != 1:
            return None
        summary_match = _unique_summary_match(
            _build_summary_index(destination_tasks), canonical_summary
        )
        if summary_match and source_task.uid and summary_match.uid:
            self._store_mapping(pair_state, *(self._mapping_args(source_name, source_task.uid, summary_match.uid)))
        return summary_match

    def _mapping_args(self, source_name: str, source_uid: str, destination_uid: str) -> tuple[str, str]:
        """Return google uid and icloud uid in storage order."""
        if source_name == "google":
            return source_uid, destination_uid
        return destination_uid, source_uid

    def _store_mapping(
        self, pair_state: dict[str, Any], google_uid: str, icloud_uid: str
    ) -> None:
        """Persist a bidirectional mapping."""
        pair_state["mapping"]["google_to_icloud"][google_uid] = icloud_uid
        pair_state["mapping"]["icloud_to_google"][icloud_uid] = google_uid

    def _remove_stale_mapping(
        self, pair_state: dict[str, Any], source_name: str, source_uid: str
    ) -> None:
        """Remove one stale mapping before fallback matching."""
        if source_name == "google":
            icloud_uid = pair_state["mapping"]["google_to_icloud"].pop(source_uid, None)
            if icloud_uid:
                pair_state["mapping"]["icloud_to_google"].pop(icloud_uid, None)
            return
        google_uid = pair_state["mapping"]["icloud_to_google"].pop(source_uid, None)
        if google_uid:
            pair_state["mapping"]["google_to_icloud"].pop(google_uid, None)

    def _record_completion(self, pair_state: dict[str, Any], task: NormalizedTask) -> None:
        """Remember a completion timestamp for cleanup fallback."""
        key = f"{task.source}:{task.uid or task.summary}"
        pair_state["completion_history"][key] = (
            task.completed or dt_util.utcnow()
        ).isoformat()

    def _lookup_completion(
        self, pair_state: dict[str, Any], task: NormalizedTask
    ) -> datetime | None:
        """Get a recorded completion timestamp."""
        key = f"{task.source}:{task.uid or task.summary}"
        value = pair_state["completion_history"].get(key)
        return dt_util.parse_datetime(value) if value else None

    def _remove_task_from_mapping(self, pair_state: dict[str, Any], task: NormalizedTask) -> None:
        """Remove all mapping and completion state for one task."""
        mapping = pair_state["mapping"]
        completion_history = pair_state["completion_history"]
        key = f"{task.source}:{task.uid or task.summary}"
        completion_history.pop(key, None)

        if task.source == "google" and task.uid:
            pair_state["status_snapshot"].pop(task.uid, None)
            icloud_uid = mapping["google_to_icloud"].pop(task.uid, None)
            if icloud_uid:
                mapping["icloud_to_google"].pop(icloud_uid, None)
        elif task.source == "icloud" and task.uid:
            google_uid = mapping["icloud_to_google"].pop(task.uid, None)
            if google_uid:
                pair_state["status_snapshot"].pop(google_uid, None)
                mapping["google_to_icloud"].pop(google_uid, None)

    def _entity_id_for_source(self, source_name: str) -> str:
        """Return configured entity id for a source name."""
        if source_name == "google":
            return self.google_entity_id
        return self.icloud_entity_id


def canonicalize_summary(value: str | None) -> str:
    """Normalize summary values for matching."""
    return (value or "").strip().casefold()


@callback
def build_manager_config(entry_id: str, title: str, data: dict[str, Any]) -> dict[str, Any]:
    """Convert config entry data into a runtime config dict."""
    return {
        "entry_id": entry_id,
        CONF_NAME: data[CONF_NAME],
        CONF_PAIR_ID: data[CONF_PAIR_ID],
        CONF_GOOGLE_ENTITY_ID: data[CONF_GOOGLE_ENTITY_ID],
        CONF_ICLOUD_ENTITY_ID: data[CONF_ICLOUD_ENTITY_ID],
        CONF_SYNC_DIRECTION: data[CONF_SYNC_DIRECTION],
        CONF_SYNC_COMPLETION: data[CONF_SYNC_COMPLETION],
        CONF_SYNC_DUE_DATES: data[CONF_SYNC_DUE_DATES],
        CONF_SYNC_DESCRIPTIONS: data[CONF_SYNC_DESCRIPTIONS],
        CONF_CLEANUP_ENABLED: data[CONF_CLEANUP_ENABLED],
        CONF_CLEANUP_DAYS: data[CONF_CLEANUP_DAYS],
        CONF_DEBOUNCE_SECONDS: data[CONF_DEBOUNCE_SECONDS],
        "title": title,
    }


def _build_summary_index(tasks: list[NormalizedTask]) -> dict[str, list[NormalizedTask]]:
    index: dict[str, list[NormalizedTask]] = {}
    for task in tasks:
        key = task.canonical_summary
        if not key:
            continue
        index.setdefault(key, []).append(task)
    return index


def _unique_summary_match(
    index: dict[str, list[NormalizedTask]], canonical_summary: str
) -> NormalizedTask | None:
    matches = index.get(canonical_summary, [])
    if len(matches) == 1:
        return matches[0]
    return None


def _same_due(first: date | datetime | None, second: date | datetime | None) -> bool:
    if first is None or second is None:
        return first is second
    first_is_datetime = isinstance(first, datetime)
    if first_is_datetime != isinstance(second, datetime):
        return _due_date(first) == _due_date(second)
    return first == second


def _due_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return dt_util.as_local(value).date() if value.tzinfo else value.date()
    return value


def _parse_due(value: str | None) -> date | datetime | None:
    if not value:
        return None
    parsed_date = dt_util.parse_date(value)
    if parsed_date is not None:
        return parsed_date
    return dt_util.parse_datetime(value)
