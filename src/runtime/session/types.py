"""Public contracts and value objects for session persistence.

The session record is deliberately separate from the provider ``Message``.
This leaves room for future compaction and non-message records without
changing the model-facing message contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from core.types import Message

RecordType = Literal["message", "compaction", "session_info"]


@dataclass(frozen=True)
class SessionRecord:
    """Persisted message envelope used to reconstruct a session branch."""

    version: int
    session_id: str
    message_id: str
    parent_id: str | None
    operation_id: str
    message: Message | None
    record_type: RecordType = "message"
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Keep the discriminated record payload internally consistent."""
        if self.record_type not in ("message", "compaction", "session_info"):
            raise ValueError(f"Unknown session record type: {self.record_type}")
        if self.record_type == "message" and self.message is None:
            raise ValueError("Message records must contain a message")
        if self.record_type in ("compaction", "session_info") and self.message is not None:
            label = "Compaction" if self.record_type == "compaction" else "Session info"
            raise ValueError(f"{label} records cannot contain a message")


@dataclass(frozen=True)
class ActivePathSnapshot:
    """One immutable view of the active branch and its derived records."""

    path: tuple[SessionRecord, ...]
    messages: tuple[Message, ...]
    compactions: tuple[SessionRecord, ...]
    latest_compaction: SessionRecord | None


@dataclass(frozen=True)
class SessionTreeNode:
    """UI-neutral summary of one persisted session-tree record."""

    message_id: str
    parent_id: str | None
    record_type: RecordType
    role: str
    preview: str
    depth: int
    children_ids: tuple[str, ...]
    is_active: bool
    is_leaf: bool


class SessionStore(Protocol):
    """Persistence contract consumed by Harness, independent of storage."""

    def append(self, message: Message) -> None:
        ...

    def append_compaction(self, metadata: dict[str, Any]) -> None:
        ...

    def append_session_info(self, name: str) -> None:
        ...

    def read(self) -> list[Message]:
        ...

    def active_snapshot(self) -> ActivePathSnapshot:
        ...

    def read_all(self) -> list[SessionRecord]:
        ...

    def checkout(self, message_id: str | None) -> None:
        ...

    def rollback(self, message_id: str | None = None) -> None:
        ...

    def resolve_message_id(self, value: str) -> str:
        ...
