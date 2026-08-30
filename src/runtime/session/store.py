"""Append-only JSONL session storage and lightweight branch navigation."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

from core.errors import SessionError
from core.types import Message
from .codec import decode_message, encode_message
from .tree import SessionTree
from .types import ActivePathSnapshot, RecordType, SessionRecord


class JsonlSessionStore:
    """Persist messages while keeping branch navigation storage-local.

    The append-only log stores every branch. The sidecar ``.head`` stores the
    active leaf, so future compact records can be added without changing the
    provider or Harness contracts.
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
        parsed = self._parse_records(records)
        self._tree = SessionTree(parsed, self._load_head())
        self._active_snapshot: ActivePathSnapshot | None = None

    @property
    def current_leaf_id(self) -> str | None:
        return self._tree.leaf_id

    @property
    def head_path(self) -> Path:
        return self.path.with_suffix(".head")

    def append(self, message: Message) -> None:
        message_id = uuid.uuid4().hex
        record = {
            "version": self.version,
            "type": "message",
            "session_id": self.session_id,
            "message_id": message_id,
            "parent_id": self._tree.leaf_id,
            "operation_id": self.operation_id,
            "message": encode_message(message),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SessionError(f"Could not append session history: {exc}") from exc
        parsed = SessionRecord(
            self.version,
            self.session_id,
            message_id,
            self._tree.leaf_id,
            self.operation_id,
            message,
        )
        self._tree.add(parsed)
        self._invalidate_active_snapshot()
        self._write_head()

    def append_compaction(self, metadata: dict[str, Any]) -> None:
        """Append a non-message compaction marker without deleting history."""
        message_id = uuid.uuid4().hex
        record = {
            "version": self.version,
            "type": "compaction",
            "session_id": self.session_id,
            "message_id": message_id,
            "parent_id": self._tree.leaf_id,
            "operation_id": self.operation_id,
            "metadata": dict(metadata),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SessionError(f"Could not append compaction record: {exc}") from exc
        self._tree.add(
            SessionRecord(
                self.version,
                self.session_id,
                message_id,
                self._tree.leaf_id,
                self.operation_id,
                None,
                "compaction",
                dict(metadata),
            )
        )
        self._invalidate_active_snapshot()
        self._write_head()

    def read(self) -> list[Message]:
        return list(self.active_snapshot().messages)

    def read_all(self) -> list[SessionRecord]:
        return self._tree.all_records()

    def get_record(self, message_id: str) -> SessionRecord | None:
        return self._tree.get_record(message_id)

    def current_path(self) -> list[SessionRecord]:
        return list(self.active_snapshot().path)

    def compactions(self) -> list[SessionRecord]:
        return list(self.active_snapshot().compactions)

    def active_snapshot(self) -> ActivePathSnapshot:
        """Return one cached active-branch view for all read-side consumers."""
        if self._active_snapshot is None:
            path = tuple(self._tree.current_path())
            messages = tuple(record.message for record in path if record.message is not None)
            compactions = tuple(record for record in path if record.record_type == "compaction")
            self._active_snapshot = ActivePathSnapshot(
                path,
                messages,
                compactions,
                compactions[-1] if compactions else None,
            )
        return self._active_snapshot

    def children(self, message_id: str) -> list[SessionRecord]:
        return self._tree.children(message_id)

    def resolve_message_id(self, value: str) -> str:
        return self._tree.resolve_message_id(value)

    def previous_turn_leaf_id(self) -> str | None:
        return self._tree.previous_turn_leaf_id()

    def checkout(self, message_id: str | None) -> None:
        self._tree.checkout(message_id)
        self._invalidate_active_snapshot()
        self._write_head()

    def rollback(self, message_id: str | None = None) -> None:
        self._tree.rollback(message_id)
        self._invalidate_active_snapshot()

    def _invalidate_active_snapshot(self) -> None:
        self._active_snapshot = None

    def _load_head(self) -> str | None:
        if not self.head_path.is_file():
            return None
        try:
            value = self.head_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SessionError(f"Could not read session head: {exc}") from exc
        return value or None

    def _write_head(self) -> None:
        temporary = self.head_path.with_suffix(".head.tmp")
        try:
            temporary.write_text(self._tree.leaf_id or "", encoding="utf-8")
            os.replace(temporary, self.head_path)
        except OSError as exc:
            raise SessionError(f"Could not write session head: {exc}") from exc

    def _parse_records(self, records: list[dict[str, Any]]) -> list[SessionRecord]:
        parsed: list[SessionRecord] = []
        for record in records:
            try:
                raw_type = record.get("type", "message")
                if raw_type not in ("message", "compaction"):
                    raise ValueError(f"Unknown session record type: {raw_type}")
                record_type: RecordType = raw_type
                parsed.append(
                    SessionRecord(
                        int(record.get("version", self.version)),
                        str(record["session_id"]),
                        str(record["message_id"]),
                        record.get("parent_id"),
                        str(record["operation_id"]),
                        decode_message(record["message"]) if record_type == "message" else None,
                        record_type,
                        dict(record.get("metadata") or {}) or None,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SessionError(f"Invalid session record: {exc}") from exc
        return parsed

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
                        raise SessionError(f"Invalid session JSON at line {number}: {exc.msg}") from exc
                    if not isinstance(record, dict):
                        raise SessionError(f"Invalid session record at line {number}")
                    if record.get("type", "message") == "message" and "message" not in record:
                        raise SessionError(f"Missing message in session record at line {number}")
                    yield record
        except OSError as exc:
            raise SessionError(f"Could not read session history: {exc}") from exc
