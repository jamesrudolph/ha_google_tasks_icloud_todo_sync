"""Pure sync logic for three-way todo reconciliation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .const import ENDPOINTS, STATUS_COMPLETED, STATUS_NEEDS_ACTION
from .models import (
    CanonicalTodoItem,
    ConflictEntry,
    EndpointSnapshotItem,
    ItemResolution,
    SyncOperation,
    SyncPlan,
    normalize_title_key,
)

_MISSING = object()


@dataclass(frozen=True, slots=True)
class _FieldResult:
    value: Any
    winner: str | None
    losers: list[str]
    conflict: bool


def _record_from_state(data: dict[str, Any]) -> CanonicalTodoItem:
    return CanonicalTodoItem.from_dict(data)


def _resolve_field(
    field: str,
    base_value: Any,
    current_records: dict[str, CanonicalTodoItem],
    tiebreak_order: list[str],
) -> _FieldResult:
    """Resolve one field with three-way merge semantics."""
    current_values = {
        endpoint: getattr(record, field) for endpoint, record in current_records.items()
    }
    changed = {
        endpoint: value
        for endpoint, value in current_values.items()
        if base_value is _MISSING or value != base_value
    }
    if not changed:
        return _FieldResult(base_value, None, [], False)

    if field == "status" and any(
        value == STATUS_COMPLETED for value in changed.values()
    ):
        winner = next(
            endpoint
            for endpoint in tiebreak_order
            if changed.get(endpoint) == STATUS_COMPLETED
        )
        conflict = len(set(changed.values())) > 1
        losers = [
            endpoint
            for endpoint, value in changed.items()
            if endpoint != winner and value != STATUS_COMPLETED
        ]
        return _FieldResult(STATUS_COMPLETED, winner, losers, conflict)

    ordered_changed = [endpoint for endpoint in tiebreak_order if endpoint in changed]
    unique_values = {changed[endpoint] for endpoint in ordered_changed}
    winner = ordered_changed[0]
    if len(unique_values) == 1:
        return _FieldResult(changed[winner], winner, [], False)

    losers = [
        endpoint
        for endpoint in ordered_changed[1:]
        if changed[endpoint] != changed[winner]
    ]
    return _FieldResult(changed[winner], winner, losers, True)


def _resolve_record(
    base: CanonicalTodoItem | object,
    current_records: dict[str, CanonicalTodoItem],
    tiebreak_order: list[str],
    cycle_id: int,
) -> tuple[CanonicalTodoItem, list[ConflictEntry], str | None]:
    """Resolve a full item record."""
    base_values: dict[str, Any]
    if base is _MISSING:
        base_values = {
            "summary": _MISSING,
            "status": _MISSING,
            "due": _MISSING,
            "description": _MISSING,
        }
    else:
        assert isinstance(base, CanonicalTodoItem)
        base_values = {
            "summary": base.summary,
            "status": base.status,
            "due": base.due,
            "description": base.description,
        }

    conflicts: list[ConflictEntry] = []
    last_changed_by: str | None = None
    resolved: dict[str, Any] = {}
    for field in ("summary", "status", "due", "description"):
        result = _resolve_field(
            field,
            base_values[field],
            current_records,
            tiebreak_order,
        )
        resolved[field] = result.value
        if result.winner and last_changed_by is None:
            last_changed_by = result.winner
        if result.conflict and result.winner:
            conflicts.append(
                ConflictEntry(
                    field=field,
                    winner=result.winner,
                    losers=result.losers,
                    cycle_id=cycle_id,
                )
            )

    return (
        CanonicalTodoItem(
            summary=resolved["summary"] or "",
            status=resolved["status"] or STATUS_NEEDS_ACTION,
            due=resolved["due"],
            description=resolved["description"],
        ),
        conflicts,
        last_changed_by,
    )


def _stable_items(items: list[EndpointSnapshotItem]) -> list[EndpointSnapshotItem]:
    """Return items in stable bootstrap pairing order."""
    return sorted(
        items,
        key=lambda item: (item.record.due or "", item.position, item.uid),
    )


def _desired_order(
    group_state: dict[str, Any],
    resolutions: dict[str, ItemResolution],
    snapshots: dict[str, list[EndpointSnapshotItem]],
    tiebreak_order: list[str],
    order_sync: bool,
) -> list[str]:
    """Choose the canonical order for live items."""
    live_ids = [
        canonical_id
        for canonical_id, resolution in resolutions.items()
        if any(resolution.desired_presence.values())
    ]
    if not live_ids:
        return []

    by_uid = {
        endpoint: {
            uid: canonical_id
            for canonical_id, resolution in resolutions.items()
            if (uid := resolution.ids.get(endpoint)) is not None
        }
        for endpoint in ENDPOINTS
    }
    current_orders = {
        endpoint: [
            by_uid[endpoint][item.uid]
            for item in snapshots[endpoint]
            if item.uid in by_uid[endpoint]
            and any(resolutions[by_uid[endpoint][item.uid]].desired_presence.values())
        ]
        for endpoint in ENDPOINTS
    }
    previous_orders = group_state.get("endpoint_orders", {})
    stored_order = [
        canonical_id
        for canonical_id in group_state.get("order", [])
        if canonical_id in live_ids
    ]

    if order_sync:
        base_order: list[str] | None = None
        for endpoint in tiebreak_order:
            if current_orders[endpoint] != previous_orders.get(endpoint, []):
                base_order = current_orders[endpoint]
                break
        if base_order is None:
            base_order = stored_order or current_orders[tiebreak_order[0]]
    else:
        base_order = stored_order or current_orders[tiebreak_order[0]]

    desired: list[str] = []
    for canonical_id in base_order:
        if canonical_id in live_ids and canonical_id not in desired:
            desired.append(canonical_id)

    for endpoint in tiebreak_order:
        for canonical_id in current_orders[endpoint]:
            if canonical_id in live_ids and canonical_id not in desired:
                desired.append(canonical_id)

    for canonical_id in live_ids:
        if canonical_id not in desired:
            desired.append(canonical_id)
    return desired


def _resolve_existing_item(
    canonical_id: str,
    stored_item: dict[str, Any],
    current_items: dict[str, EndpointSnapshotItem | None],
    tiebreak_order: list[str],
    cycle_id: int,
    bootstrap: bool,
) -> tuple[ItemResolution, list[SyncOperation]]:
    """Resolve one previously-known canonical item."""
    base = _record_from_state(stored_item["base"])
    ids = {endpoint: stored_item.get("ids", {}).get(endpoint) for endpoint in ENDPOINTS}
    present_records = {
        endpoint: item.record
        for endpoint, item in current_items.items()
        if item is not None
    }
    tracked_missing = [
        endpoint
        for endpoint in ENDPOINTS
        if ids.get(endpoint) is not None and current_items[endpoint] is None
    ]

    if not present_records:
        return (
            ItemResolution(
                canonical_id=canonical_id,
                ids=ids,
                resolved=base,
                desired_presence={endpoint: False for endpoint in ENDPOINTS},
                first_seen_on=stored_item.get("first_seen_on"),
                link_method=stored_item.get("link_method", "uid"),
                last_changed_by=None,
                conflicts=[],
                deleted_in=tracked_missing[0] if tracked_missing else None,
            ),
            [],
        )

    if (
        tracked_missing
        and not bootstrap
        and all(record == base for record in present_records.values())
    ):
        delete_ops = [
            SyncOperation(
                action="delete",
                endpoint=endpoint,
                canonical_id=canonical_id,
                uid=current_items[endpoint].uid,
            )
            for endpoint in ENDPOINTS
            if current_items[endpoint] is not None
        ]
        return (
            ItemResolution(
                canonical_id=canonical_id,
                ids=ids,
                resolved=base,
                desired_presence={endpoint: False for endpoint in ENDPOINTS},
                first_seen_on=stored_item.get("first_seen_on"),
                link_method=stored_item.get("link_method", "uid"),
                last_changed_by=None,
                conflicts=[],
                deleted_in=tracked_missing[0],
            ),
            delete_ops,
        )

    resolved, conflicts, last_changed_by = _resolve_record(
        base, present_records, tiebreak_order, cycle_id
    )
    desired_presence = {endpoint: True for endpoint in ENDPOINTS}
    operations: list[SyncOperation] = []
    for endpoint in ENDPOINTS:
        current_item = current_items[endpoint]
        if current_item is None:
            operations.append(
                SyncOperation(
                    action="create",
                    endpoint=endpoint,
                    canonical_id=canonical_id,
                    record=resolved,
                )
            )
            continue
        if current_item.record != resolved:
            operations.append(
                SyncOperation(
                    action="update",
                    endpoint=endpoint,
                    canonical_id=canonical_id,
                    uid=current_item.uid,
                    record=resolved,
                )
            )

    return (
        ItemResolution(
            canonical_id=canonical_id,
            ids=ids,
            resolved=resolved,
            desired_presence=desired_presence,
            first_seen_on=stored_item.get("first_seen_on"),
            link_method=stored_item.get("link_method", "uid"),
            last_changed_by=last_changed_by,
            conflicts=conflicts,
            deleted_in=None,
        ),
        operations,
    )


def _resolve_new_items(
    unmatched: dict[str, list[EndpointSnapshotItem]],
    tiebreak_order: list[str],
    cycle_id: int,
) -> tuple[dict[str, ItemResolution], list[SyncOperation]]:
    """Turn unmatched live items into canonical items."""
    grouped: dict[str, dict[str, list[EndpointSnapshotItem]]] = defaultdict(
        lambda: {endpoint: [] for endpoint in ENDPOINTS}
    )
    for endpoint in ENDPOINTS:
        for item in _stable_items(unmatched[endpoint]):
            grouped[normalize_title_key(item.record.summary)][endpoint].append(item)

    resolutions: dict[str, ItemResolution] = {}
    operations: list[SyncOperation] = []
    for title_key in sorted(grouped):
        per_endpoint = grouped[title_key]
        group_size = max(len(items) for items in per_endpoint.values())
        for index in range(group_size):
            present = {
                endpoint: per_endpoint[endpoint][index]
                for endpoint in ENDPOINTS
                if index < len(per_endpoint[endpoint])
            }
            if not present:
                continue

            resolved, conflicts, last_changed_by = _resolve_record(
                _MISSING,
                {endpoint: item.record for endpoint, item in present.items()},
                tiebreak_order,
                cycle_id,
            )
            canonical_id = uuid4().hex
            ids = {
                endpoint: present[endpoint].uid if endpoint in present else None
                for endpoint in ENDPOINTS
            }
            desired_presence = {endpoint: True for endpoint in ENDPOINTS}
            resolution = ItemResolution(
                canonical_id=canonical_id,
                ids=ids,
                resolved=resolved,
                desired_presence=desired_presence,
                first_seen_on=next(iter(present)),
                link_method="title_match" if len(present) > 1 else "created",
                last_changed_by=last_changed_by,
                conflicts=conflicts,
            )
            resolutions[canonical_id] = resolution
            for endpoint in ENDPOINTS:
                if endpoint not in present:
                    operations.append(
                        SyncOperation(
                            action="create",
                            endpoint=endpoint,
                            canonical_id=canonical_id,
                            record=resolved,
                        )
                    )
    return resolutions, operations


def plan_sync(
    group_state: dict[str, Any],
    snapshots: dict[str, list[EndpointSnapshotItem]],
    *,
    tiebreak_order: list[str],
    mass_delete_count: int,
    mass_delete_percent: int,
    order_sync: bool,
    rebuild: bool = False,
) -> SyncPlan:
    """Create a sync plan from current snapshots and persisted state."""
    bootstrap = rebuild or not group_state.get("bootstrap_completed", False)
    current_counts = {endpoint: len(snapshots[endpoint]) for endpoint in ENDPOINTS}
    current_by_uid = {
        endpoint: {item.uid: item for item in snapshots[endpoint]}
        for endpoint in ENDPOINTS
    }
    used_uids = {endpoint: set() for endpoint in ENDPOINTS}
    cycle_id = int(group_state.get("cycle_counter", 0)) + 1

    resolutions: dict[str, ItemResolution] = {}
    operations: list[SyncOperation] = []
    for canonical_id, stored_item in group_state.get("items", {}).items():
        current_items = {
            endpoint: current_by_uid[endpoint].get(
                stored_item.get("ids", {}).get(endpoint)
            )
            for endpoint in ENDPOINTS
        }
        for endpoint, item in current_items.items():
            if item is not None:
                used_uids[endpoint].add(item.uid)

        resolution, item_operations = _resolve_existing_item(
            canonical_id,
            stored_item,
            current_items,
            tiebreak_order,
            cycle_id,
            bootstrap,
        )
        resolutions[canonical_id] = resolution
        operations.extend(item_operations)

    unmatched = {
        endpoint: [
            item for item in snapshots[endpoint] if item.uid not in used_uids[endpoint]
        ]
        for endpoint in ENDPOINTS
    }
    new_resolutions, new_operations = _resolve_new_items(
        unmatched,
        tiebreak_order,
        cycle_id,
    )
    resolutions.update(new_resolutions)
    operations.extend(new_operations)

    delete_counts = {endpoint: 0 for endpoint in ENDPOINTS}
    for operation in operations:
        if operation.action == "delete":
            delete_counts[operation.endpoint] += 1

    abort_reason: str | None = None
    if not bootstrap:
        for endpoint in ENDPOINTS:
            delete_count = delete_counts[endpoint]
            current_count = current_counts[endpoint]
            delete_percent = (
                (delete_count / current_count * 100) if current_count else 0
            )
            if delete_count > mass_delete_count or (
                delete_count > 1 and delete_percent > mass_delete_percent
            ):
                abort_reason = (
                    f"Mass-delete guard blocked {delete_count} deletes on {endpoint} "
                    f"({delete_percent:.0f}% of the list)"
                )
                break

    desired_order = _desired_order(
        group_state,
        resolutions,
        snapshots,
        tiebreak_order,
        order_sync,
    )

    conflicts = sum(len(resolution.conflicts) for resolution in resolutions.values())
    summary = {
        "creates": sum(1 for operation in operations if operation.action == "create"),
        "updates": sum(1 for operation in operations if operation.action == "update"),
        "deletes": sum(1 for operation in operations if operation.action == "delete"),
        "conflicts": conflicts,
    }
    if abort_reason:
        operations = []

    return SyncPlan(
        operations=operations,
        resolutions=resolutions,
        desired_order=desired_order,
        delete_counts=delete_counts,
        current_counts=current_counts,
        bootstrap=bootstrap,
        abort_reason=abort_reason,
        summary=summary,
    )
