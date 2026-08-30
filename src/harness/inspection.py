"""Read-only inspection of the last model request sent by the Harness."""

from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.types import Message, ModelRequest, ToolResult


@dataclass(frozen=True)
class ContextSnapshot:
    """Immutable metadata and request captured at the provider boundary."""

    request: ModelRequest
    sequence: int
    captured_at: datetime


class ContextInspector:
    """Keep the latest request without affecting the agent transcript."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: ContextSnapshot | None = None
        self._sequence = 0

    def capture(self, request: ModelRequest) -> ContextSnapshot:
        """Capture a defensive copy so later state changes cannot alter it."""
        with self._lock:
            self._sequence += 1
            snapshot = ContextSnapshot(
                request=copy.deepcopy(request),
                sequence=self._sequence,
                captured_at=datetime.now(timezone.utc),
            )
            self._snapshot = snapshot
            return copy.deepcopy(snapshot)

    def snapshot(self) -> ContextSnapshot | None:
        """Return a defensive copy of the latest request, if one exists."""
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def clear(self) -> None:
        with self._lock:
            self._snapshot = None


class InspectingProvider:
    """Provider decorator that observes the exact request before delegation."""

    def __init__(self, provider, inspector: ContextInspector) -> None:
        self.provider = provider
        self.inspector = inspector

    def stream(self, request: ModelRequest):
        self.inspector.capture(request)
        yield from self.provider.stream(request)

    def __getattr__(self, name: str):
        # Preserve optional provider attributes such as ``config`` for callers.
        return getattr(self.provider, name)


_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")


def _redact_text(value: str) -> str:
    value = _BEARER_TOKEN.sub(r"\1[REDACTED]", value)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", value)


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(name): _redact_value(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    return value


def _message_dict(message: Message, *, redact: bool) -> dict[str, Any]:
    redact_value = _redact_value if redact else lambda value, key=None: value
    return {
        "role": message.role,
        "content": _redact_text(message.content) if redact else message.content,
        "tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": redact_value(call.arguments),
            }
            for call in message.tool_calls
        ],
        "tool_result": _tool_result_dict(message.tool_result, redact=redact),
    }


def _tool_result_dict(result: ToolResult | None, *, redact: bool) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "tool_call_id": result.tool_call_id,
        "name": result.name,
        "content": _redact_text(result.content) if redact else result.content,
        "is_error": result.is_error,
        "truncated": result.truncated,
        "truncated_by": result.truncated_by,
        "total_lines": result.total_lines,
        "total_bytes": result.total_bytes,
        "output_lines": result.output_lines,
        "output_bytes": result.output_bytes,
    }


def snapshot_payload(snapshot: ContextSnapshot, *, redact: bool = True) -> dict[str, Any]:
    """Convert a snapshot to a stable, JSON-serializable diagnostic payload."""
    request = snapshot.request
    return {
        "sequence": snapshot.sequence,
        "captured_at": snapshot.captured_at.isoformat(),
        "system_prompt": _redact_text(request.system_prompt) if redact else request.system_prompt,
        "messages": [_message_dict(message, redact=redact) for message in request.messages],
        "tools": [
            {
                "name": spec.name,
                "description": _redact_text(spec.description) if redact else spec.description,
                "input_schema": _redact_value(spec.input_schema) if redact else spec.input_schema,
            }
            for spec in request.tools
        ],
    }


def format_context_snapshot(snapshot: ContextSnapshot, *, redact: bool = True) -> str:
    """Render a snapshot for ``/show_context`` or machine-readable diagnostics."""
    return json.dumps(snapshot_payload(snapshot, redact=redact), ensure_ascii=False, indent=2)


__all__ = [
    "ContextInspector",
    "ContextSnapshot",
    "InspectingProvider",
    "format_context_snapshot",
    "snapshot_payload",
]
