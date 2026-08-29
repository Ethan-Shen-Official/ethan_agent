from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterator, Protocol

from core.errors import SessionError
from core.types import Message, ToolCall, ToolResult


class SessionStore(Protocol):
    """Persistence contract for an ordered conversation transcript."""

    def append(self, message: Message) -> None:
        ...

    def read(self) -> list[Message]:
        ...


def default_session_path(cwd: str | os.PathLike[str]) -> Path:
    """Return a stable per-workspace path under .agent/sessions."""
    root = str(Path(cwd).resolve())
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
    base = Path(
        os.environ.get(
            "CODING_AGENT_SESSION_DIR",
            str(Path(root) / ".agent" / "sessions"),
        )
    ).expanduser()
    return base / f"{digest}.jsonl"


class JsonlSessionStore:
    """Small append-only JSONL transcript store.

    Each record carries stable identifiers even though P0 Message objects do
    not expose them yet. This leaves room for branching and operation-level
    metadata without changing the Provider message contract.
    """

    version = 1

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        session_id: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = list(self._records())
        existing_session_id = records[0].get("session_id") if records else None
        self.session_id = session_id or existing_session_id or uuid.uuid4().hex
        self.operation_id = operation_id or uuid.uuid4().hex
        self._leaf_id: str | None = None
        for record in records:
            self._leaf_id = record.get("message_id") or self._leaf_id

    def append(self, message: Message) -> None:
        message_id = uuid.uuid4().hex
        record = {
            "version": self.version,
            "session_id": self.session_id,
            "message_id": message_id,
            "parent_id": self._leaf_id,
            "operation_id": self.operation_id,
            "message": _encode_message(message),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SessionError(f"Could not append session history: {exc}") from exc
        self._leaf_id = message_id

    def read(self) -> list[Message]:
        return [_decode_message(record["message"]) for record in self._records()]

    def _records(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise SessionError(
                            f"Invalid session JSON at line {number}: {exc.msg}"
                        ) from exc
                    if not isinstance(record, dict):
                        raise SessionError(f"Invalid session record at line {number}")
                    if "message" not in record:
                        raise SessionError(f"Missing message in session record at line {number}")
                    yield record
        except OSError as exc:
            raise SessionError(f"Could not read session history: {exc}") from exc


def _encode_message(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ],
        "tool_result": _encode_tool_result(message.tool_result),
    }


def _encode_tool_result(result: ToolResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "tool_call_id": result.tool_call_id,
        "name": result.name,
        "content": result.content,
        "is_error": result.is_error,
        "truncated": result.truncated,
        "truncated_by": result.truncated_by,
        "total_lines": result.total_lines,
        "total_bytes": result.total_bytes,
        "output_lines": result.output_lines,
        "output_bytes": result.output_bytes,
    }


def _decode_message(data: Any) -> Message:
    if not isinstance(data, dict):
        raise SessionError("Session message must be an object")
    try:
        calls = [
            ToolCall(
                str(item["id"]),
                str(item["name"]),
                dict(item.get("arguments") or {}),
            )
            for item in data.get("tool_calls") or []
        ]
        raw_result = data.get("tool_result")
        result = None
        if raw_result is not None:
            if not isinstance(raw_result, dict):
                raise TypeError("tool_result must be an object")
            result = ToolResult(
                str(raw_result["tool_call_id"]),
                str(raw_result["name"]),
                str(raw_result.get("content", "")),
                bool(raw_result.get("is_error", False)),
                bool(raw_result.get("truncated", False)),
                raw_result.get("truncated_by"),
                raw_result.get("total_lines"),
                raw_result.get("total_bytes"),
                raw_result.get("output_lines"),
                raw_result.get("output_bytes"),
            )
        return Message(
            role=data["role"],
            content=str(data.get("content", "")),
            tool_calls=calls,
            tool_result=result,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionError(f"Invalid session message: {exc}") from exc
