"""Session manager binding a session store to loop state."""

from __future__ import annotations

from os import PathLike
from pathlib import Path

from core.errors import SessionError
from core.state import LoopState
from runtime.compact import TokenLedger
from runtime.session import (
    JsonlSessionStore,
    SessionStore,
    SessionTreeNode,
    build_session_tree_view,
    delete_session_path,
    default_session_path,
    latest_session_path,
    list_session_paths,
    resolve_session_path,
)


class SessionManager:
    """Own session selection, branch navigation, state restoration and writes."""

    def __init__(
        self,
        cwd: str | PathLike[str],
        *,
        session_path: str | PathLike[str] | None = None,
        resume: bool = False,
        session_store: SessionStore | None = None,
    ) -> None:
        self.cwd = Path(cwd).resolve()
        if session_store is not None:
            self.store = session_store
        else:
            selected_path = session_path
            if selected_path is None and resume:
                selected_path = latest_session_path(self.cwd)
            self.store = JsonlSessionStore(selected_path or default_session_path(self.cwd))

        self.state = LoopState(messages=self._restored_messages())
        self.persisted_message_count = len(self.state.messages)
        self.token_ledger = TokenLedger.from_messages(self.state.messages)

    @property
    def session_id(self) -> str:
        value = getattr(self.store, "session_id", None)
        if not isinstance(value, str) or not value:
            raise SessionError("configured session store does not expose a session id")
        return value

    @property
    def session_path(self) -> Path:
        value = getattr(self.store, "path", None)
        if value is None:
            raise SessionError("configured session store does not expose a session path")
        return Path(value)

    @property
    def session_name(self) -> str | None:
        getter = getattr(self.store, "get_session_name", None)
        return getter() if getter is not None else None

    def set_session_name(self, name: str) -> None:
        append = getattr(self.store, "append_session_info", None)
        if append is None:
            raise SessionError("No active session; use /new or /resume first")
        append(name.strip())

    def session_catalog(self) -> list[dict[str, object]]:
        catalog: list[dict[str, object]] = []
        for path in list_session_paths(self.cwd):
            store = JsonlSessionStore(path)
            messages = store.read()
            first_user = next(
                (message.content for message in messages if message.role == "user"),
                "",
            )
            catalog.append(
                {
                    "path": path,
                    "id": store.session_id if store.read_all() else path.stem,
                    "name": store.get_session_name(),
                    "first_prompt": first_user,
                    "modified": path.stat().st_mtime,
                }
            )
        return catalog

    def session_tree(self) -> list[SessionTreeNode]:
        read_all = getattr(self.store, "read_all", None)
        current_path = getattr(self.store, "current_path", None)
        if read_all is None or current_path is None:
            raise SessionError("Configured session store does not support tree inspection")
        return build_session_tree_view(
            list(read_all()),
            list(current_path()),
            getattr(self.store, "current_leaf_id", None),
        )

    def new_session(self) -> Path:
        path = default_session_path(self.cwd)
        store = JsonlSessionStore(path)
        path.touch(exist_ok=False)
        self.activate(store)
        return path

    def resume_session(self, identifier: str) -> Path:
        path = resolve_session_path(self.cwd, identifier)
        self.activate(JsonlSessionStore(path))
        return path

    def drop_session(self, identifier: str) -> Path:
        value = identifier.strip() if isinstance(identifier, str) else ""
        if not value:
            raise SessionError("A session id is required; the active session cannot be dropped")
        target = resolve_session_path(self.cwd, value)
        if target.resolve() == self.session_path.resolve():
            raise SessionError("Cannot drop the active session; use /new or /resume instead")
        return delete_session_path(self.cwd, target)

    def activate(self, store: SessionStore) -> None:
        """Make a store active and restore its branch into a fresh LoopState."""
        self.store = store
        self.state = LoopState(messages=self._restored_messages())
        self.persisted_message_count = len(self.state.messages)
        self.token_ledger.reset(self.state.messages)

    def checkout(self, message_id: str | None) -> None:
        checkout = getattr(self.store, "checkout", None)
        if checkout is None:
            raise SessionError("Configured session store does not support branches")
        checkout(message_id)
        self._reload_active_state()

    def resolve_message_id(self, value: str) -> str:
        resolver = getattr(self.store, "resolve_message_id", None)
        if resolver is None:
            raise SessionError("Configured session store does not support message lookup")
        return resolver(value)

    def rollback(self, message_id: str | None = None) -> None:
        rollback = getattr(self.store, "rollback", None)
        if rollback is None:
            raise SessionError("Configured session store does not support rollback")
        rollback(message_id)
        self._reload_active_state()

    def persist_pending(self) -> None:
        while self.persisted_message_count < len(self.state.messages):
            message = self.state.messages[self.persisted_message_count]
            self.store.append(message)
            self.token_ledger.append(message)
            self.persisted_message_count += 1

    def active_snapshot(self):
        getter = getattr(self.store, "active_snapshot", None)
        return getter() if getter is not None else None

    def _restored_messages(self) -> list:
        snapshot = self.active_snapshot()
        return list(snapshot.messages) if snapshot is not None else self.store.read()

    def _reload_active_state(self) -> None:
        self.state.messages = self._restored_messages()
        self.state.turn_count = 0
        self.state.stop_reason = None
        self.state.recovery_failures = 0
        self.persisted_message_count = len(self.state.messages)
        self.token_ledger.reset(self.state.messages)


# Compatibility name retained for callers of the earlier split module.
SessionRuntime = SessionManager

__all__ = ["SessionManager", "SessionRuntime"]
