"""Unit tests for the pure sync engine."""

from __future__ import annotations

from custom_components.ha_google_tasks_icloud_todo_sync.const import (
    ENDPOINT_GOOGLE,
    ENDPOINT_HOME_ASSISTANT,
    ENDPOINT_ICLOUD,
)
from custom_components.ha_google_tasks_icloud_todo_sync.engine import plan_sync
from custom_components.ha_google_tasks_icloud_todo_sync.models import (
    CanonicalTodoItem,
    EndpointSnapshotItem,
)
from custom_components.ha_google_tasks_icloud_todo_sync.storage import make_group_state


def _item(endpoint: str, uid: str, position: int, **kwargs) -> EndpointSnapshotItem:
    return EndpointSnapshotItem(
        endpoint=endpoint,
        uid=uid,
        position=position,
        record=CanonicalTodoItem(**kwargs),
    )


def _state_with_single_item() -> dict:
    state = make_group_state(settings={})
    state["bootstrap_completed"] = True
    state["items"] = {
        "abc": {
            "canonical_id": "abc",
            "ids": {
                ENDPOINT_HOME_ASSISTANT: "h1",
                ENDPOINT_GOOGLE: "g1",
                ENDPOINT_ICLOUD: "i1",
            },
            "base": {
                "summary": "Milk",
                "status": "needs_action",
                "due": "2026-10-12",
                "description": None,
            },
            "first_seen_on": ENDPOINT_HOME_ASSISTANT,
            "link_method": "uid",
        }
    }
    return state


def test_plan_sync_merges_different_fields_without_conflict() -> None:
    state = _state_with_single_item()
    snapshots = {
        ENDPOINT_HOME_ASSISTANT: [
            _item(
                ENDPOINT_HOME_ASSISTANT,
                "h1",
                0,
                summary="Milk and Eggs",
                status="needs_action",
                due="2026-10-12",
                description=None,
            )
        ],
        ENDPOINT_GOOGLE: [
            _item(
                ENDPOINT_GOOGLE,
                "g1",
                0,
                summary="Milk",
                status="needs_action",
                due="2026-10-12",
                description="Buy lactose-free",
            )
        ],
        ENDPOINT_ICLOUD: [
            _item(
                ENDPOINT_ICLOUD,
                "i1",
                0,
                summary="Milk",
                status="needs_action",
                due="2026-10-13",
                description=None,
            )
        ],
    }
    plan = plan_sync(
        state,
        snapshots,
        tiebreak_order=[
            ENDPOINT_HOME_ASSISTANT,
            ENDPOINT_ICLOUD,
            ENDPOINT_GOOGLE,
        ],
        mass_delete_count=10,
        mass_delete_percent=75,
        order_sync=True,
    )

    resolution = plan.resolutions["abc"]
    assert resolution.resolved == CanonicalTodoItem(
        summary="Milk and Eggs",
        status="needs_action",
        due="2026-10-13",
        description="Buy lactose-free",
    )
    assert {(operation.action, operation.endpoint) for operation in plan.operations} == {
        ("update", ENDPOINT_GOOGLE),
        ("update", ENDPOINT_HOME_ASSISTANT),
        ("update", ENDPOINT_ICLOUD),
    }


def test_plan_sync_edit_beats_delete_and_recreates_missing_side() -> None:
    state = _state_with_single_item()
    snapshots = {
        ENDPOINT_HOME_ASSISTANT: [],
        ENDPOINT_GOOGLE: [
            _item(
                ENDPOINT_GOOGLE,
                "g1",
                0,
                summary="Milk",
                status="needs_action",
                due="2026-10-12",
                description="updated",
            )
        ],
        ENDPOINT_ICLOUD: [
            _item(
                ENDPOINT_ICLOUD,
                "i1",
                0,
                summary="Milk",
                status="needs_action",
                due="2026-10-12",
                description=None,
            )
        ],
    }
    plan = plan_sync(
        state,
        snapshots,
        tiebreak_order=[
            ENDPOINT_HOME_ASSISTANT,
            ENDPOINT_ICLOUD,
            ENDPOINT_GOOGLE,
        ],
        mass_delete_count=10,
        mass_delete_percent=75,
        order_sync=True,
    )

    assert ("create", ENDPOINT_HOME_ASSISTANT) in {
        (operation.action, operation.endpoint) for operation in plan.operations
    }
    assert plan.resolutions["abc"].desired_presence[ENDPOINT_HOME_ASSISTANT] is True


def test_plan_sync_bootstrap_pairs_duplicate_titles_by_position() -> None:
    state = make_group_state(settings={})
    snapshots = {
        ENDPOINT_HOME_ASSISTANT: [
            _item(
                ENDPOINT_HOME_ASSISTANT,
                "h1",
                0,
                summary="Milk",
                status="needs_action",
                due="2026-10-11",
                description="1",
            ),
            _item(
                ENDPOINT_HOME_ASSISTANT,
                "h2",
                1,
                summary="Milk",
                status="needs_action",
                due="2026-10-12",
                description="2",
            ),
        ],
        ENDPOINT_GOOGLE: [
            _item(
                ENDPOINT_GOOGLE,
                "g1",
                0,
                summary="Milk",
                status="needs_action",
                due="2026-10-11",
                description=None,
            )
        ],
        ENDPOINT_ICLOUD: [],
    }
    plan = plan_sync(
        state,
        snapshots,
        tiebreak_order=[
            ENDPOINT_HOME_ASSISTANT,
            ENDPOINT_ICLOUD,
            ENDPOINT_GOOGLE,
        ],
        mass_delete_count=10,
        mass_delete_percent=75,
        order_sync=True,
    )

    assert len(plan.resolutions) == 2
    assert plan.summary["creates"] == 4
    assert all(resolution.link_method in {"title_match", "created"} for resolution in plan.resolutions.values())

