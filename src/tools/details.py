"""Out-of-band details produced by tools.

The core ToolResult contract intentionally stays text-only.  Rich data such as
diffs, patches and full-output paths lives here and is keyed by tool call id so
TUI/ACP adapters can consume it without changing core message types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any


@dataclass
class ToolDetailsStore:
    """Thread-safe bounded store for transient tool execution details."""

    max_entries: int = 256
    _values: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def put(self, call_id: str, details: dict[str, Any]) -> None:
        if not call_id:
            return
        with self._lock:
            self._values[call_id] = dict(details)
            while len(self._values) > max(1, self.max_entries):
                self._values.pop(next(iter(self._values)))

    def get(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._values.get(call_id)
            return dict(value) if value is not None else None

    def pop(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._values.pop(call_id, None)
            return dict(value) if value is not None else None

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


__all__ = ["ToolDetailsStore"]
