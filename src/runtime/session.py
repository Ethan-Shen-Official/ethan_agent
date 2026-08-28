from __future__ import annotations

from typing import Protocol

from core.types import Message


class SessionStore(Protocol):
    """Reserved persistence contract; P0 keeps state in memory."""

    def append(self, message: Message) -> None:
        ...

    def read(self) -> list[Message]:
        ...
