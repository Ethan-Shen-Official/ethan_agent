"""In-memory session tree navigation independent of any storage backend."""

from __future__ import annotations

from .types import SessionRecord
from core.errors import SessionError


class SessionTree:
    """Maintain records and the active leaf for one session transcript."""

    def __init__(self, records: list[SessionRecord], leaf_id: str | None = None) -> None:
        self._records = {record.message_id: record for record in records}
        self._order = [record.message_id for record in records]
        self._leaf_id = leaf_id or (self._order[-1] if self._order else None)
        if self._leaf_id is not None and self._leaf_id not in self._records:
            raise SessionError("Session head points to a missing message")

    @property
    def leaf_id(self) -> str | None:
        return self._leaf_id

    def add(self, record: SessionRecord) -> None:
        if record.message_id in self._records:
            raise SessionError(f"Duplicate session message: {record.message_id}")
        self._records[record.message_id] = record
        self._order.append(record.message_id)
        self._leaf_id = record.message_id

    def all_records(self) -> list[SessionRecord]:
        return [self._records[message_id] for message_id in self._order]

    def get_record(self, message_id: str) -> SessionRecord | None:
        return self._records.get(message_id)

    def current_path(self) -> list[SessionRecord]:
        return self._path_to(self._leaf_id)

    def children(self, message_id: str) -> list[SessionRecord]:
        return [record for record in self.all_records() if record.parent_id == message_id]

    def resolve_message_id(self, value: str) -> str:
        if value in self._records:
            return value
        matches = [message_id for message_id in self._records if message_id.startswith(value)]
        if not matches:
            raise SessionError(f"Unknown session message: {value}")
        if len(matches) > 1:
            raise SessionError(f"Ambiguous session message prefix: {value}")
        return matches[0]

    def previous_turn_leaf_id(self) -> str | None:
        path = self.current_path()
        last_user = next(
            (
                index
                for index in range(len(path) - 1, -1, -1)
                if path[index].message is not None and path[index].message.role == "user"
            ),
            None,
        )
        if last_user is None:
            return None
        return path[last_user - 1].message_id if last_user else None

    def checkout(self, message_id: str | None) -> None:
        if message_id is not None and message_id not in self._records:
            raise SessionError(f"Unknown session message: {message_id}")
        path = self._path_to(message_id)
        if not self._is_complete_boundary(path):
            raise SessionError("Cannot checkout inside an incomplete tool turn")
        self._leaf_id = message_id

    def rollback(self, message_id: str | None = None) -> None:
        target = self.previous_turn_leaf_id() if message_id is None else message_id
        self.checkout(target)

    def _path_to(self, message_id: str | None) -> list[SessionRecord]:
        if message_id is None:
            return []
        chain: list[SessionRecord] = []
        seen: set[str] = set()
        current: str | None = message_id
        while current is not None:
            if current in seen:
                raise SessionError("Cycle detected in session parent links")
            seen.add(current)
            record = self._records.get(current)
            if record is None:
                raise SessionError(f"Missing parent record {current}")
            chain.append(record)
            current = record.parent_id
        chain.reverse()
        return chain

    @staticmethod
    def _is_complete_boundary(path: list[SessionRecord]) -> bool:
        pending: set[str] = set()
        for record in path:
            message = record.message
            if message is None:
                continue
            if message.role == "assistant":
                if pending:
                    return False
                pending = {call.id for call in message.tool_calls}
            elif message.role == "tool":
                if message.tool_result is None or message.tool_result.tool_call_id not in pending:
                    return False
                pending.remove(message.tool_result.tool_call_id)
            elif pending:
                return False
        return not pending
