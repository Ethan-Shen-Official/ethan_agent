"""Interactive session overlays kept separate from the TUI event loop."""

from __future__ import annotations

from .state import TranscriptItem
from ..views.session import format_session_entry, is_active_entry, is_active_identifier
from ..views.tree import build_tree_overlay


class OverlayController:
    """Own resume/drop/tree state transitions for a :class:`TuiApplication`.

    The application remains the owner of rendering, input editor, and
    lifecycle. This controller only translates selector keys into harness
    operations, keeping the main event loop small and readable.
    """

    def __init__(self, app) -> None:
        self.app = app

    @property
    def state(self):
        return self.app.state

    @property
    def harness(self):
        return self.app.harness

    def open_resume(self) -> None:
        catalog = self.harness.session_catalog()
        if not catalog:
            self.state.transcript.append(TranscriptItem("assistant", "[resume] no persisted sessions"))
            self.state.status = "ready"
            return
        self.state.overlay_kind = "resume"
        self.state.overlay_title = "Available sessions"
        self.state.overlay_items = [format_session_entry(entry) for entry in catalog]
        self.state.overlay_roles = []
        self.state.overlay_ids = [str(entry.get("id", "")) for entry in catalog]
        self.state.overlay_index = 0
        self.state.overlay_scroll = 0
        self.state.status = "selecting session"
        self.app.editor.clear()

    def open_drop(self, command: str) -> None:
        parts = command.split()
        if len(parts) > 2:
            self.app._run_command(command)
            return
        if len(parts) == 2:
            if is_active_identifier(parts[1], self.harness.session_id):
                self.state.transcript.append(
                    TranscriptItem("assistant", "[drop error] Cannot drop the active session; use /new or /resume instead")
                )
                self.state.status = "ready"
                return
            self.state.overlay_kind = "drop"
            self.state.overlay_title = ""
            self.state.overlay_items = []
            self.state.overlay_roles = []
            self.state.overlay_ids = []
            self.state.overlay_scroll = 0
            self.state.overlay_value = parts[1]
            self.state.status = "waiting for confirmation"
            self.app.editor.clear()
            return

        catalog = [
            entry
            for entry in self.harness.session_catalog()
            if not is_active_entry(entry, self.harness.session_id, getattr(self.harness, "session_path", None))
        ]
        if not catalog:
            self.state.transcript.append(TranscriptItem("assistant", "[drop] no deletable sessions"))
            self.state.status = "ready"
            return
        self.state.overlay_kind = "drop"
        self.state.overlay_title = "Delete Session"
        self.state.overlay_items = [format_session_entry(entry) for entry in catalog]
        self.state.overlay_roles = []
        self.state.overlay_ids = [str(entry.get("id", "")) for entry in catalog]
        self.state.overlay_index = 0
        self.state.overlay_scroll = 0
        self.state.overlay_value = ""
        self.state.status = "selecting session to delete"
        self.app.editor.clear()

    def open_tree(self) -> None:
        try:
            nodes = self.harness.session_tree()
        except Exception as exc:
            self.state.transcript.append(TranscriptItem("assistant", f"[tree error] {exc}"))
            return
        if not nodes:
            self.state.transcript.append(TranscriptItem("assistant", "[tree] session is empty"))
            return
        tree = build_tree_overlay(nodes)
        self.state.overlay_kind = "tree"
        self.state.overlay_title = "Session Tree"
        self.state.overlay_items = tree.items
        self.state.overlay_roles = tree.roles
        self.state.overlay_ids = tree.identifiers
        self.state.overlay_index = tree.selected
        self.state.overlay_scroll = 0
        self.state.status = "selecting tree node"
        self.app.editor.clear()

    def handle_key(self, key: str) -> None:
        kind = self.state.overlay_kind
        if kind == "resume":
            self._handle_resume(key)
        elif kind == "tree":
            self._handle_tree(key)
        elif kind == "drop":
            self._handle_drop(key)

    def _handle_resume(self, key: str) -> None:
        if key == "ESC":
            self.close("[resume] cancelled")
            return
        if key in {"UP", "DOWN"}:
            self.app._move_overlay_selection(-1 if key == "UP" else 1)
            self.app._draw()
            return
        selected = self.app.editor.handle(key)
        if selected is None:
            self.app._draw()
            return
        value = selected.strip()
        catalog = self.harness.session_catalog()
        if value.isdigit() and 1 <= int(value) <= len(catalog):
            identifier = str(catalog[int(value) - 1]["id"])
        elif value:
            identifier = value
        else:
            identifier = self.state.overlay_ids[self.state.overlay_index]
        try:
            path = self.harness.resume_session(identifier)
            self.app._sync_session_view()
            label = getattr(self.harness, "session_name", None) or "(unnamed)"
            self.state.transcript.append(TranscriptItem("assistant", f"[resume] {label} - {self.harness.session_id} ({path.name})"))
            self.state.status = "ready"
        except Exception as exc:
            self.close(f"[resume error] {exc}")
        self.app.editor.clear()
        self.app._draw()

    def _handle_tree(self, key: str) -> None:
        if key == "ESC":
            self.close("[tree] cancelled")
            return
        if key in {"LEFT", "RIGHT"}:
            self.app._move_overlay_selection((-1 if key == "LEFT" else 1) * self.app._overlay_item_capacity())
            self.app._draw()
            return
        if key in {"UP", "DOWN"}:
            self.app._move_overlay_selection(-1 if key == "UP" else 1)
            self.app._draw()
            return
        selected = self.app.editor.handle(key)
        if selected is None and key not in {"\r", "\n"}:
            self.app._draw()
            return
        identifier = self.state.overlay_ids[self.state.overlay_index] if self.state.overlay_ids else ""
        if selected and selected.strip():
            identifier = selected.strip()
        try:
            self.harness.checkout(identifier or None)
            self.app._sync_session_view()
            self.close(f"[checkout] active message: {identifier or 'root'}")
        except Exception as exc:
            self.close(f"[checkout error] {exc}")

    def _handle_drop(self, key: str) -> None:
        if key == "ESC":
            self.close("[drop] cancelled")
            return
        if not self.state.overlay_value:
            if key in {"UP", "DOWN"}:
                self.app._move_overlay_selection(-1 if key == "UP" else 1)
                self.app._draw()
                return
            selected = self.app.editor.handle(key)
            if selected is None:
                self.app._draw()
                return
            value = selected.strip()
            if value.isdigit() and 1 <= int(value) <= len(self.state.overlay_ids):
                identifier = self.state.overlay_ids[int(value) - 1]
            elif value:
                identifier = value
            else:
                identifier = self.state.overlay_ids[self.state.overlay_index]
            if is_active_identifier(identifier, self.harness.session_id):
                self.close("[drop error] Cannot drop the active session; use /new or /resume instead")
                return
            self.state.overlay_value = identifier
            self.state.status = "waiting for confirmation"
            self.app.editor.clear()
            self.app._draw()
            return
        answer = key.strip().lower()
        if answer not in {"y", "yes", "n", "no", "\r", "\n"}:
            self.app._draw()
            return
        if answer in {"\r", "\n"} or answer not in {"y", "yes"}:
            self.close("[drop] cancelled")
            return
        try:
            deleted = self.harness.drop_session(self.state.overlay_value)
            self.close(f"[drop] deleted: {deleted.name}")
        except Exception as exc:
            self.close(f"[drop error] {exc}")

    def close(self, message: str) -> None:
        self.state.overlay_kind = None
        self.state.overlay_title = ""
        self.state.overlay_items = []
        self.state.overlay_roles = []
        self.state.overlay_ids = []
        self.state.overlay_scroll = 0
        self.state.overlay_value = ""
        self.state.status = "ready"
        self.app.editor.clear()
        if message:
            self.state.transcript.append(TranscriptItem("assistant", message))
        self.app._draw()


__all__ = ["OverlayController"]
