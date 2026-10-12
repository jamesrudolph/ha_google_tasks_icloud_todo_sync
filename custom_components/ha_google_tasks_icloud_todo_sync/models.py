"""Shared models for the sync integration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONFLICT_LOG_LIMIT,
    ENDPOINTS,
    STATUS_NEEDS_ACTION,
)


def _clean_text(value: str | None) -> str | None:
    """Normalize text fields for stable comparisons."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class CanonicalTodoItem:
    """Normalized todo item fields used by the sync engine."""

    summary: str
    status: str = STATUS_NEEDS_ACTION
    due: str | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """Return a JSON-serializable representation."""
        return {
            "summary": self.summary,
            "status": self.status,
            "due": self.due,
            "description": self.description,
        }

    def hash(self) -> str:
        """Return a stable hash for debugging and diagnostics."""
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(payload.encode(), usedforsecurity=False).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalTodoItem:
        """Create an item from persisted state."""
        return cls(
            summary=data["summary"],
            status=data["status"],
            due=data.get("due"),
            description=_clean_text(data.get("description")),
        )


@dataclass(frozen=True, slots=True)
class EndpointSnapshotItem:
    """A current item observed from one endpoint."""

    endpoint: str
    uid: str
    record: CanonicalTodoItem
    position: int


@dataclass(frozen=True, slots=True)
class SyncOperation:
    """A write operation to apply to one endpoint."""

    action: str
    endpoint: str
    canonical_id: str
    uid: str | None = None
    record: CanonicalTodoItem | None = None


@dataclass(slots=True)
class ConflictEntry:
    """A single conflict resolution record."""

    field: str
    winner: str
    losers: list[str]
    cycle_id: int

    def as_dict(self) -> dict[str, Any]:
        """Serialize the conflict entry."""
        return {
            "field": self.field,
            "winner": self.winner,
            "losers": self.losers,
            "cycle_id": self.cycle_id,
        }


@dataclass(slots=True)
class ItemResolution:
    """Resolved state for a canonical item in one sync cycle."""

    canonical_id: str
    ids: dict[str, str | None]
    resolved: CanonicalTodoItem
    desired_presence: dict[str, bool]
    first_seen_on: str | None
    link_method: str
    last_changed_by: str | None
    conflicts: list[ConflictEntry] = field(default_factory=list)
    deleted_in: str | None = None


@dataclass(slots=True)
class SyncPlan:
    """The full output of a sync-engine cycle."""

    operations: list[SyncOperation]
    resolutions: dict[str, ItemResolution]
    desired_order: list[str]
    delete_counts: dict[str, int]
    current_counts: dict[str, int]
    bootstrap: bool
    abort_reason: str | None = None
    summary: dict[str, int] = field(default_factory=dict)


def make_empty_endpoint_map(default: Any = None) -> dict[str, Any]:
    """Return a dict with all endpoints pre-populated."""
    return {endpoint: default for endpoint in ENDPOINTS}


def capped_conflicts(conflicts: list[ConflictEntry]) -> list[ConflictEntry]:
    """Keep only the most recent stored conflicts."""
    return conflicts[-CONFLICT_LOG_LIMIT:]


def normalize_title_key(summary: str) -> str:
    """Return a stable key for bootstrap title matching."""
    return " ".join(summary.split()).casefold()
