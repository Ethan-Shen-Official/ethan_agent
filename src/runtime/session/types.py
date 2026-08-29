"""Public contracts and value objects for session persistence.

The session record is deliberately separate from the provider ``Message``.
This leaves room for future compaction and non-message records without
changing the model-facing message contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.types import Message


@dataclass(frozen=True)
class SessionRecord:
    """Persisted message envelope used to reconstruct a session branch."""

    version: int
    session_id: str
    message_id: str
    parent_id: str | None
    operation_id: str
    message: Message
    record_type: str = "message"


class SessionStore(Protocol):
    """Persistence contract consumed by Harness, independent of storage."""

    def append(self, message: Message) -> None:
        ...

    def read(self) -> list[Message]:
        ...

    def checkout(self, message_id: str | None) -> None:
        ...

    def rollback(self, message_id: str | None = None) -> None:
        ...

    def resolve_message_id(self, value: str) -> str:
        ...
