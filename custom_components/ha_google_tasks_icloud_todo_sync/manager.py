"""Runtime sync manager."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)

from .adapter import EndpointSnapshot, TodoSyncAdapter
from .const import (
    CONF_DEBOUNCE_SECONDS,
    CONF_GOOGLE_ENTITY_ID,
    CONF_GROUP_NAME,
    CONF_HOME_ASSISTANT_ENTITY_ID,
    CONF_ICLOUD_ENTITY_ID,
    CONF_MASS_DELETE_COUNT,
    CONF_MASS_DELETE_PERCENT,
    CONF_ORDER_SYNC,
    CONF_POLL_INTERVAL,
    CONF_TIEBREAK_ORDER,
    CONF_TOMBSTONE_DAYS,
    CONF_UNAVAILABLE_GRACE_MINUTES,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_MASS_DELETE_COUNT,
    DEFAULT_MASS_DELETE_PERCENT,
    DEFAULT_ORDER_SYNC,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DEFAULT_TIEBREAK_ORDER,
    DEFAULT_TOMBSTONE_DAYS,
    DEFAULT_UNAVAILABLE_GRACE_MINUTES,
    ENDPOINT_GOOGLE,
    ENDPOINT_HOME_ASSISTANT,
    ENDPOINT_ICLOUD,
    ENDPOINTS,
    INTEGRATION_VERSION,
    SYNC_STATUS_ERROR,
    SYNC_STATUS_IDLE,
    SYNC_STATUS_MASS_DELETE_BLOCKED,
    SYNC_STATUS_OK,
    SYNC_STATUS_SYNCING,
    SYNC_STATUS_UNAVAILABLE,
)
from .engine import plan_sync
from .repairs import (
    async_create_mass_delete_issue,
    async_create_unavailable_issue,
    async_delete_mass_delete_issue,
    async_delete_unavailable_issue,
)
from .storage import SyncStore, as_iso, reset_group_state, tombstone_payload, utcnow

_LOGGER = logging.getLogger(__name__)


def entry_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return merged entry options with defaults applied."""
    return {
        CONF_TIEBREAK_ORDER: list(
            entry.options.get(CONF_TIEBREAK_ORDER, DEFAULT_TIEBREAK_ORDER)
        ),
        CONF_POLL_INTERVAL: int(
            entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_MINUTES)
        ),
        CONF_DEBOUNCE_SECONDS: int(
            entry.options.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS)
        ),
        CONF_TOMBSTONE_DAYS: int(
            entry.options.get(CONF_TOMBSTONE_DAYS, DEFAULT_TOMBSTONE_DAYS)
        ),
        CONF_MASS_DELETE_COUNT: int(
            entry.options.get(CONF_MASS_DELETE_COUNT, DEFAULT_MASS_DELETE_COUNT)
        ),
        CONF_MASS_DELETE_PERCENT: int(
            entry.options.get(CONF_MASS_DELETE_PERCENT, DEFAULT_MASS_DELETE_PERCENT)
        ),
        CONF_ORDER_SYNC: bool(entry.options.get(CONF_ORDER_SYNC, DEFAULT_ORDER_SYNC)),
        CONF_UNAVAILABLE_GRACE_MINUTES: int(
            entry.options.get(
                CONF_UNAVAILABLE_GRACE_MINUTES,
                DEFAULT_UNAVAILABLE_GRACE_MINUTES,
            )
        ),
    }


def settings_snapshot(entry: ConfigEntry) -> dict[str, Any]:
    """Return the persisted settings snapshot for diagnostics/state."""
    options = entry_options(entry)
    return {
        "group_name": entry.data[CONF_GROUP_NAME],
        "entities": {
            ENDPOINT_HOME_ASSISTANT: entry.data[CONF_HOME_ASSISTANT_ENTITY_ID],
            ENDPOINT_GOOGLE: entry.data[CONF_GOOGLE_ENTITY_ID],
            ENDPOINT_ICLOUD: entry.data[CONF_ICLOUD_ENTITY_ID],
        },
        **options,
    }


class SyncManager:
    """Manage one config entry's sync lifecycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: SyncStore,
        adapter: TodoSyncAdapter,
    ) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.store = store
        self.adapter = adapter
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._scheduled: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()
        self._queued = False
        self._queued_rebuild = False
        self._status = SYNC_STATUS_IDLE
        self._pending_count = 0
        self._last_sync_at: datetime | None = None
        self._last_error: str | None = None
        self._unavailable_since: datetime | None = None

    @property
    def entity_ids(self) -> dict[str, str]:
        """Return the configured entities by endpoint."""
        return {
            ENDPOINT_HOME_ASSISTANT: self.entry.data[CONF_HOME_ASSISTANT_ENTITY_ID],
            ENDPOINT_GOOGLE: self.entry.data[CONF_GOOGLE_ENTITY_ID],
            ENDPOINT_ICLOUD: self.entry.data[CONF_ICLOUD_ENTITY_ID],
        }

    @property
    def group_name(self) -> str:
        """Return the configured sync-group name."""
        return self.entry.data[CONF_GROUP_NAME]

    @property
    def status(self) -> str:
        """Return the current manager status."""
        return self._status

    @property
    def pending_count(self) -> int:
        """Return the current pending operation count."""
        return self._pending_count

    @property
    def last_sync_at(self) -> datetime | None:
        """Return the last successful or attempted sync time."""
        return self._last_sync_at

    @property
    def last_error(self) -> str | None:
        """Return the last sync error, if any."""
        return self._last_error

    async def async_setup(self) -> None:
        """Start listeners and the periodic poller."""
        for entity_id in self.entity_ids.values():
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass,
                    [entity_id],
                    self._async_handle_state_change,
                )
            )

        poll_interval = timedelta(minutes=entry_options(self.entry)[CONF_POLL_INTERVAL])
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._async_handle_interval,
                poll_interval,
            )
        )
        await self.async_sync_now()

    async def async_unload(self) -> None:
        """Stop listeners and timers."""
        if self._scheduled is not None:
            self._scheduled()
            self._scheduled = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @callback
    def async_add_listener(self, listener) -> CALLBACK_TYPE:
        """Register a callback for status updates."""
        self._listeners.append(listener)

        @callback
        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def _set_status(
        self,
        status: str,
        *,
        pending_count: int | None = None,
        error: str | None = None,
    ) -> None:
        self._status = status
        if pending_count is not None:
            self._pending_count = pending_count
        self._last_error = error
        self._notify()

    @callback
    def _async_handle_state_change(self, _: Event) -> None:
        self.request_sync()

    @callback
    def _async_handle_interval(self, _: datetime) -> None:
        self.request_sync()

    @callback
    def request_sync(self, *, rebuild: bool = False) -> None:
        """Queue a debounced sync request."""
        self._queued = True
        self._queued_rebuild = self._queued_rebuild or rebuild

        if self._scheduled is not None:
            return

        debounce_seconds = entry_options(self.entry)[CONF_DEBOUNCE_SECONDS]
        if debounce_seconds <= 0:
            self.hass.async_create_task(self._async_process_queue())
            return

        self._scheduled = async_call_later(
            self.hass,
            debounce_seconds,
            self._async_execute_scheduled,
        )

    async def async_sync_now(self, *, rebuild: bool = False) -> None:
        """Run a sync immediately."""
        self._queued = True
        self._queued_rebuild = self._queued_rebuild or rebuild
        await self._async_process_queue()

    async def async_rebuild_mapping(self) -> None:
        """Run a no-delete mapping rebuild immediately."""
        await self.async_sync_now(rebuild=True)

    async def _async_execute_scheduled(self, _: datetime) -> None:
        self._scheduled = None
        await self._async_process_queue()

    async def _async_process_queue(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            if self._scheduled is not None:
                self._scheduled()
                self._scheduled = None
            while self._queued:
                rebuild = self._queued_rebuild
                self._queued = False
                self._queued_rebuild = False
                await self._async_sync_once(rebuild=rebuild)

    async def _async_fetch_snapshots(
        self,
    ) -> tuple[dict[str, EndpointSnapshot], list[str]]:
        snapshots: dict[str, EndpointSnapshot] = {}
        unavailable: list[str] = []
        for endpoint, entity_id in self.entity_ids.items():
            snapshot = await self.adapter.async_fetch(endpoint, entity_id)
            if snapshot is None:
                unavailable.append(endpoint)
                continue
            snapshots[endpoint] = snapshot
        return snapshots, unavailable

    async def _async_sync_once(self, *, rebuild: bool) -> None:
        """Execute one full sync cycle."""
        now = utcnow()
        current_group = deepcopy(
            self.store.get_group(
                self.entry.entry_id,
                settings=settings_snapshot(self.entry),
                now=now,
            )
        )
        self.store.purge_expired_tombstones(current_group, now=now)
        self._set_status(SYNC_STATUS_SYNCING, pending_count=0)

        try:
            snapshot_data, unavailable = await self._async_fetch_snapshots()
            if unavailable:
                await self._async_handle_unavailable(current_group, unavailable, now)
                return

            self._unavailable_since = None
            async_delete_unavailable_issue(self.hass, self.entry.entry_id)

            working_group = (
                reset_group_state(
                    current_group,
                    settings=settings_snapshot(self.entry),
                    now=now,
                )
                if rebuild
                else deepcopy(current_group)
            )
            working_group["settings"] = settings_snapshot(self.entry)

            plan = plan_sync(
                working_group,
                {endpoint: snapshot_data[endpoint].items for endpoint in ENDPOINTS},
                tiebreak_order=entry_options(self.entry)[CONF_TIEBREAK_ORDER],
                mass_delete_count=entry_options(self.entry)[CONF_MASS_DELETE_COUNT],
                mass_delete_percent=entry_options(self.entry)[CONF_MASS_DELETE_PERCENT],
                order_sync=entry_options(self.entry)[CONF_ORDER_SYNC],
                rebuild=rebuild,
            )
            self._set_status(
                SYNC_STATUS_SYNCING,
                pending_count=len(plan.operations),
            )

            if plan.abort_reason:
                async_create_mass_delete_issue(
                    self.hass,
                    self.entry.entry_id,
                    plan.abort_reason,
                )
                self.store.record_cycle(
                    working_group,
                    result=SYNC_STATUS_MASS_DELETE_BLOCKED,
                    creates=plan.summary["creates"],
                    updates=plan.summary["updates"],
                    deletes=plan.summary["deletes"],
                    conflicts=plan.summary["conflicts"],
                )
                self.store.replace_group(self.entry.entry_id, working_group)
                await self.store.async_save()
                self._last_sync_at = now
                self._set_status(SYNC_STATUS_MASS_DELETE_BLOCKED, pending_count=0)
                return

            async_delete_mass_delete_issue(self.hass, self.entry.entry_id)

            for operation in plan.operations:
                entity_id = self.entity_ids[operation.endpoint]
                if operation.action == "create":
                    assert operation.record is not None
                    created_uid = await self.adapter.async_create(
                        entity_id,
                        operation.record,
                    )
                    plan.resolutions[operation.canonical_id].ids[operation.endpoint] = (
                        created_uid
                    )
                    continue
                if operation.action == "update":
                    assert operation.uid is not None
                    assert operation.record is not None
                    await self.adapter.async_update(
                        entity_id,
                        operation.uid,
                        operation.record,
                    )
                    continue
                if operation.action == "delete":
                    assert operation.uid is not None
                    await self.adapter.async_delete(entity_id, operation.uid)

            if entry_options(self.entry)[CONF_ORDER_SYNC]:
                for endpoint in ENDPOINTS:
                    if not snapshot_data[endpoint].capabilities.move:
                        continue
                    ordered_uids = [
                        plan.resolutions[canonical_id].ids[endpoint]
                        for canonical_id in plan.desired_order
                        if plan.resolutions[canonical_id].ids.get(endpoint)
                        and plan.resolutions[canonical_id].desired_presence[endpoint]
                    ]
                    await self.adapter.async_reorder(
                        entity_id=self.entity_ids[endpoint],
                        ordered_uids=ordered_uids,
                    )

            final_snapshot_data, final_unavailable = await self._async_fetch_snapshots()
            if final_unavailable:
                await self._async_handle_unavailable(
                    current_group,
                    final_unavailable,
                    utcnow(),
                )
                return

            self._apply_final_state(
                working_group,
                plan,
                final_snapshot_data,
                now=utcnow(),
            )
            self.store.record_cycle(
                working_group,
                result=SYNC_STATUS_OK,
                creates=plan.summary["creates"],
                updates=plan.summary["updates"],
                deletes=plan.summary["deletes"],
                conflicts=plan.summary["conflicts"],
            )
            self.store.replace_group(self.entry.entry_id, working_group)
            await self.store.async_save()
            self._last_sync_at = datetime.fromisoformat(working_group["last_sync_at"])
            self._set_status(SYNC_STATUS_OK, pending_count=0)
        except Exception as err:
            _LOGGER.exception("Sync cycle failed for %s", self.entry.entry_id)
            current_group["settings"] = settings_snapshot(self.entry)
            self.store.record_cycle(
                current_group,
                result=SYNC_STATUS_ERROR,
                errors=1,
                now=utcnow(),
            )
            self.store.replace_group(self.entry.entry_id, current_group)
            await self.store.async_save()
            self._last_sync_at = utcnow()
            self._set_status(SYNC_STATUS_ERROR, pending_count=0, error=str(err))

    async def _async_handle_unavailable(
        self,
        current_group: dict[str, Any],
        unavailable: list[str],
        now: datetime,
    ) -> None:
        if self._unavailable_since is None:
            self._unavailable_since = now
        options = entry_options(self.entry)
        grace = timedelta(minutes=options[CONF_UNAVAILABLE_GRACE_MINUTES])
        if now - self._unavailable_since >= grace:
            details = ", ".join(unavailable)
            async_create_unavailable_issue(self.hass, self.entry.entry_id, details)
        current_group["settings"] = settings_snapshot(self.entry)
        self.store.record_cycle(current_group, result=SYNC_STATUS_UNAVAILABLE, now=now)
        self.store.replace_group(self.entry.entry_id, current_group)
        await self.store.async_save()
        self._last_sync_at = now
        self._set_status(SYNC_STATUS_UNAVAILABLE, pending_count=0)

    def _apply_final_state(
        self,
        group_state: dict[str, Any],
        plan,
        snapshots: dict[str, EndpointSnapshot],
        *,
        now: datetime,
    ) -> None:
        """Persist the converged final state after a successful cycle."""
        existing_items = group_state.get("items", {})
        tombstones = dict(group_state.get("tombstones", {}))
        final_by_uid = {
            endpoint: {item.uid: item for item in snapshots[endpoint].items}
            for endpoint in ENDPOINTS
        }
        final_items: dict[str, Any] = {}
        for canonical_id, resolution in plan.resolutions.items():
            previous = existing_items.get(canonical_id, {})
            if any(resolution.desired_presence.values()):
                ids: dict[str, str | None] = {}
                last_seen: dict[str, Any] = {}
                last_seen_hashes: dict[str, str | None] = {}
                for endpoint in ENDPOINTS:
                    uid = resolution.ids.get(endpoint)
                    item = final_by_uid[endpoint].get(uid) if uid is not None else None
                    ids[endpoint] = item.uid if item is not None else uid
                    if item is None:
                        last_seen[endpoint] = None
                        last_seen_hashes[endpoint] = None
                    else:
                        last_seen[endpoint] = item.record.as_dict()
                        last_seen_hashes[endpoint] = item.record.hash()

                conflict_history = previous.get("conflicts", []) + [
                    conflict.as_dict() for conflict in resolution.conflicts
                ]
                final_items[canonical_id] = {
                    "canonical_id": canonical_id,
                    "ids": ids,
                    "base": resolution.resolved.as_dict(),
                    "base_hash": resolution.resolved.hash(),
                    "last_seen": last_seen,
                    "last_seen_hashes": last_seen_hashes,
                    "first_seen_on": resolution.first_seen_on
                    or previous.get("first_seen_on"),
                    "link_method": resolution.link_method
                    or previous.get("link_method", "uid"),
                    "last_changed_by": resolution.last_changed_by,
                    "last_changed_at": as_iso(now)
                    if resolution.last_changed_by
                    else previous.get("last_changed_at"),
                    "pending": [],
                    "conflicts": conflict_history[-20:],
                }
                tombstones.pop(canonical_id, None)
                continue

            tombstones[canonical_id] = tombstone_payload(
                canonical_id=canonical_id,
                ids=resolution.ids,
                base=previous.get("base", resolution.resolved.as_dict()),
                deleted_in=resolution.deleted_in,
                tombstone_days=entry_options(self.entry)[CONF_TOMBSTONE_DAYS],
                now=now,
            )

        canonical_by_uid = {
            endpoint: {
                state["ids"][endpoint]: canonical_id
                for canonical_id, state in final_items.items()
                if state["ids"].get(endpoint) is not None
            }
            for endpoint in ENDPOINTS
        }
        group_state["items"] = final_items
        group_state["tombstones"] = tombstones
        group_state["order"] = [
            canonical_id
            for canonical_id in plan.desired_order
            if canonical_id in final_items
        ]
        group_state["endpoint_orders"] = {
            endpoint: [
                canonical_by_uid[endpoint][item.uid]
                for item in snapshots[endpoint].items
                if item.uid in canonical_by_uid[endpoint]
            ]
            for endpoint in ENDPOINTS
        }
        group_state["settings"] = settings_snapshot(self.entry)
        group_state["integration_version"] = INTEGRATION_VERSION
        group_state["bootstrap_completed"] = True

    def diagnostics_data(self) -> dict[str, Any]:
        """Return manager runtime diagnostics."""
        return {
            "status": self._status,
            "pending_count": self._pending_count,
            "last_error": self._last_error,
            "last_sync_at": self._last_sync_at.isoformat()
            if self._last_sync_at
            else None,
            "unavailable_since": self._unavailable_since.isoformat()
            if self._unavailable_since
            else None,
        }
