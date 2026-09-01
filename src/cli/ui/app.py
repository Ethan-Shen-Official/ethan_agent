"""Interactive terminal UI lifecycle, modeled after pi's event-driven mode."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from typing import TYPE_CHECKING

from ..commands import handle_repl_command, is_exit_command, resolve_command
from .input import InputEditor
from .overlay import OverlayController
from .reducer import reduce_event
from .renderer import ScreenRenderer
from .state import TranscriptItem, UiState
from .terminal import TerminalBackend

if TYPE_CHECKING:
    from harness.app import Harness
    from ..repl import ApprovalBroker


class TuiApplication:
    """Own terminal state and coordinate one background Harness operation."""

    def __init__(self, harness: "Harness", approval_broker: "ApprovalBroker") -> None:
        self.harness = harness
        self.broker = approval_broker
        self.terminal = TerminalBackend()
        self.renderer = ScreenRenderer(self.terminal)
        self.editor = InputEditor()
        self.overlay = OverlayController(self)
        self._paste_mode = False
        self.events: Queue[tuple[str, object]] = Queue()
        self.worker: threading.Thread | None = None
        # Slash commands that can call the provider (currently /compact) run
        # on the same worker channel as prompts so the input loop stays live.
        self._command_running = False
        self._command_cancel = threading.Event()
        self.state = UiState(
            cwd=str(harness.execution_env.cwd),
            session_id=harness.session_id,
            terminal_width=self.terminal.columns,
            terminal_height=self.terminal.rows,
            model_name=self._model_name(harness),
            session_name=getattr(harness, "session_name", None),
        )
        self.state.startup_context = self._discover_startup_context(self.state.cwd)
        self._restore_transcript()

    @staticmethod
    def _discover_startup_context(cwd: str) -> tuple[str, ...]:
        """Return instruction resource names shown in the initial header."""
        try:
            root = Path(cwd).resolve()
        except (OSError, ValueError):
            return ("No System Context File",)
        # Startup resources belong to the selected workspace only.  Do not
        # inherit an instruction file from a parent directory: that would
        # make two different workspaces display misleading context.
        for name in ("AGENTS.md", "CLAUDE.md"):
            if (root / name).is_file():
                return (name,)
        return ("No System Context File",)

    def _restore_transcript(self) -> None:
        """Project persisted user/assistant messages into the TUI transcript."""
        loop_state = getattr(self.harness, "state", None)
        tool_calls: dict[str, object] = {}
        for message in getattr(loop_state, "messages", ()):
            role = getattr(message, "role", "")
            content = str(getattr(message, "content", "") or "").strip()
            for call in getattr(message, "tool_calls", ()) or ():
                call_id = str(getattr(call, "id", "") or "")
                if call_id:
                    tool_calls[call_id] = call
            if role == "user" and content:
                self.state.transcript.append(TranscriptItem("user", content))
            elif role == "assistant" and content:
                self.state.transcript.append(TranscriptItem("assistant", content))
            elif role == "tool" and content:
                # Persisted tool messages can contain the full read/search
                # payload. Keep them collapsed on restore, just like live
                # tool results, and recover the name/error metadata used by
                # the dedicated preview renderer.
                result = getattr(message, "tool_result", None)
                call = tool_calls.get(str(getattr(result, "tool_call_id", "") or ""))
                self.state.transcript.append(
                    TranscriptItem(
                        "tool",
                        content,
                        collapsed=True,
                        tool_name=str(getattr(result, "name", "") or getattr(call, "name", "") or ""),
                        tool_error=bool(getattr(result, "is_error", False)),
                        tool_arguments=getattr(call, "arguments", None),
                    )
                )

    def _sync_session_view(self) -> None:
        """Replace the viewport transcript after a session/branch switch."""
        self.state.session_id = self.harness.session_id
        self.state.session_name = getattr(self.harness, "session_name", None)
        self.state.transcript.clear()
        self._restore_transcript()
        loop_state = getattr(self.harness, "state", None)
        self.state.turn = int(getattr(loop_state, "turn_count", 0) or 0)
        self.state.active_tool = None
        self.state.scroll_offset = 0
        self.state.selection_anchor = None
        self.state.selection_focus = None
        self.state.overlay_kind = None
        self.state.overlay_items = []
        self.state.overlay_roles = []
        self.state.overlay_ids = []
        self.state.overlay_scroll = 0
        self.state.overlay_value = ""

    @staticmethod
    def _model_name(harness: "Harness") -> str:
        provider = getattr(harness, "provider", None)
        config = getattr(provider, "config", None)
        return str(getattr(config, "model", "") or "")

    def run(self) -> int:
        if not self.terminal.is_tty:
            # Piped stdin has no meaningful cursor/input redraw semantics.
            # Preserve the old line-mode behavior for scripts and tests.
            from ..repl import run_repl

            return run_repl(self.harness, self.broker)

        self.terminal.start()
        self._draw()
        try:
            while self.state.mode != "exit":
                self._drain_events()
                self._update_permission_state()
                key = self.terminal.read_key()
                if key is not None:
                    self._handle_key(key)
                # Animate from the main thread; worker threads only enqueue
                # events and never write directly to the terminal.
                if self._is_busy() or self.state.status == "compacting":
                    self.state.spinner_frame = (self.state.spinner_frame + 1) % 10
                    self._draw()
                if self.worker is not None and not self.worker.is_alive() and not self.harness.is_running and not self._command_running:
                    self.worker = None
        finally:
            self.broker.cancel()
            if self.harness.is_running:
                self.harness.abort()
            if self._command_running:
                self._abort_background_command()
            if self.worker is not None:
                self.worker.join(timeout=1)
            self.terminal.stop()
        return 0

    def _draw(self) -> None:
        # The editor owns keystroke mutations; the renderer consumes the
        # structured UI state. Keep the two views synchronized before every
        # frame so typed characters and cursor movement are visible.
        self.state.input_text = self.editor.text
        self.state.cursor_position = self.editor.cursor
        self.renderer.render(self.state)

    def _start_prompt(self, text: str) -> None:
        self.state.scroll_offset = 0
        self.state.selection_anchor = None
        self.state.selection_focus = None
        self.state.copy_status = None
        self.state.mode = "working"
        self.state.status = "working"
        self.state.spinner_frame = 0
        self.state.transcript.append(TranscriptItem("user", text))

        def run() -> None:
            try:
                for event in self.harness.prompt(text):
                    self.events.put(("event", event))
            except BaseException as exc:
                self.events.put(("exception", exc))
            finally:
                self.events.put(("done", None))

        self.worker = threading.Thread(target=run, name="agent-task", daemon=True)
        self.worker.start()

    def _is_busy(self) -> bool:
        """Return whether a prompt or provider-backed slash command is active."""
        return bool(self.harness.is_running or self._command_running)

    def _start_background_command(self, command: str) -> None:
        """Run a provider-backed slash command without blocking key input."""
        if self._is_busy():
            self.state.append_system("[busy] agent is still running; use /abort")
            return
        self._command_running = True
        self._command_cancel.clear()
        self.state.selection_anchor = None
        self.state.selection_focus = None
        self.state.copy_status = None
        self.state.mode = "working"
        self.state.status = "compacting"
        self.state.spinner_frame = 0
        self.state.active_tool = None
        self.editor.clear()

        def run() -> None:
            try:
                output = StringIO()
                # handle_repl_command is a legacy print-oriented boundary. It
                # is isolated to this worker and forwarded through the event
                # queue once complete, so command output remains ordered with
                # the user's slash command.
                handle_repl_command(command, self.harness, output=output)
                self.events.put(("command_output", output.getvalue()))
            except BaseException as exc:
                self.events.put(("command_exception", exc))
            finally:
                self.events.put(("command_done", None))

        self.worker = threading.Thread(target=run, name="agent-command", daemon=True)
        self.worker.start()

    def _abort_background_command(self) -> None:
        """Request cancellation of a provider-backed command such as compact."""
        if not self._command_running:
            return
        if self._command_cancel.is_set():
            return
        self._command_cancel.set()
        provider = getattr(self.harness, "provider", None)
        abort = getattr(provider, "abort", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                # Cancellation is best effort; the worker still reports its
                # eventual result and the main loop remains responsive.
                pass

    def _drain_events(self) -> None:
        changed = False
        # Do not drain an unbounded stream in one pass.  A provider can emit
        # thousands of text deltas while a tool is running; yielding back to
        # read_key within a small frame budget keeps Esc/Ctrl+C and `/abort`
        # responsive even under heavy output.
        deadline = time.monotonic() + 0.02
        processed = 0
        while processed < 256 and (processed == 0 or time.monotonic() < deadline):
            try:
                kind, value = self.events.get_nowait()
            except Empty:
                break
            processed += 1
            changed = True
            if kind == "event":
                reduce_event(self.state, value)
            elif kind == "exception":
                self.state.active_tool = None
                self.state.mode = "error"
                self.state.status = "error"
                self.state.last_error = str(value)
                self.state.append_system(str(value), error=True)
            elif kind == "done" and self.state.mode == "working":
                self.state.mode = "idle"
                self.state.status = "completed"
            elif kind == "command_output":
                text = str(value or "").strip()
                if text:
                    self.state.transcript.append(TranscriptItem("assistant", text))
            elif kind == "command_exception":
                self.state.mode = "error"
                self.state.status = "error"
                self.state.last_error = str(value)
                self.state.append_system(str(value), error=True)
            elif kind == "command_done":
                cancelled = self._command_cancel.is_set()
                self._command_running = False
                self._command_cancel.clear()
                if self.state.mode == "working" or self.state.status == "cancelling":
                    self.state.mode = "idle"
                    self.state.status = "cancelled" if cancelled else "completed"
        if changed:
            # New runtime output follows the live prompt, as in Pi's regular
            # main-screen mode. The user can scroll up again with the wheel.
            self.state.scroll_offset = 0
            self.state.selection_anchor = None
            self.state.selection_focus = None
            self._draw()

    def _update_permission_state(self) -> None:
        if self.harness.is_running and self.broker.pending:
            if self.state.mode != "permission":
                self.state.mode = "permission"
                self.state.status = "waiting for approval"
                self._draw()
        elif self.state.mode == "permission":
            self.state.mode = "working"
            self.state.status = "working"
            self._draw()

    def _handle_key(self, key: str) -> None:
        if key == "PASTE_START":
            self._paste_mode = True
            return
        if key == "PASTE_END":
            self._paste_mode = False
            self._draw()
            return
        if key == "MOUSE_IGNORED":
            return
        if key == "\x0f":  # Ctrl+O: Pi's global tool-output expansion toggle
            self.state.tools_expanded = not self.state.tools_expanded
            self._draw()
            return
        if self._paste_mode and len(key) == 1:
            self.editor.insert_text(key)
            self._draw()
            return
        if key == "MOUSE_WHEEL_UP":
            if self._is_selector_overlay():
                self._move_overlay_selection(-1)
                self._draw()
                return
            self.state.scroll_offset += 3
            self.state.selection_anchor = None
            self.state.selection_focus = None
            self._draw()
            return
        if key == "MOUSE_WHEEL_DOWN":
            if self._is_selector_overlay():
                self._move_overlay_selection(1)
                self._draw()
                return
            self.state.scroll_offset = max(0, self.state.scroll_offset - 3)
            self.state.selection_anchor = None
            self.state.selection_focus = None
            self._draw()
            return
        if key in {"PAGEUP", "PAGEDOWN"}:
            if self._is_selector_overlay():
                self._move_overlay_selection(
                    (-1 if key == "PAGEUP" else 1) * self._overlay_item_capacity()
                )
                self._draw()
                return
            delta =  max(1, self.terminal.rows // 2)
            self.state.scroll_offset = max(0, self.state.scroll_offset + (delta if key == "PAGEUP" else -delta))
            self.state.selection_anchor = None
            self.state.selection_focus = None
            self._draw()
            return
        mouse = self._mouse_event(key)
        if mouse is not None:
            kind, x, y = mouse
            if self._is_selector_overlay() and kind in {"down", "up"}:
                if self._select_overlay_row(y):
                    self._draw()
                    return
            if kind == "down":
                self.state.selection_anchor = (x, y)
                self.state.selection_focus = (x, y)
                self.state.copy_status = None
            elif kind == "drag" and self.state.selection_anchor is not None:
                self.state.selection_focus = (x, y)
            elif kind == "up" and self.state.selection_anchor is not None:
                self.state.selection_focus = (x, y)
                self._copy_selection()
            self._draw()
            return
        if self.state.overlay_kind is not None:
            self._handle_overlay_key(key)
            return
        if key == "\x04":
            if self.editor.text:
                self.editor.clear()
                self._draw()
            else:
                self.state.mode = "exit"
            return
        if key == "\x03" and not self._is_busy() and self._has_selection():
            self._copy_selection()
            self._draw()
            return
        if key in {"\x03", "ESC"}:
            self.editor.clear()
            self.state.selection_anchor = None
            self.state.selection_focus = None
            if self.harness.is_running:
                self.broker.cancel()
                self.harness.abort()
                self.state.status = "cancelling"
            elif self._command_running:
                self._abort_background_command()
                self.state.status = "cancelling"
            self._draw()
            return

        if self._is_busy():
            if self.broker.pending:
                if key.strip().lower() in {"y", "yes", "n", "no"}:
                    self.broker.submit(key)
                    self.editor.clear()
                    self._draw()
                    return
                submitted = self.editor.handle(key)
                if submitted is not None:
                    self.broker.submit(submitted)
                    self.editor.clear()
                    self._draw()
            else:
                submitted = self.editor.handle(key)
                if submitted is not None:
                    if submitted.strip() == "/abort":
                        if self.harness.is_running:
                            self.harness.abort()
                        elif self._command_running:
                            self._abort_background_command()
                        self.state.status = "cancelling"
                    else:
                        self.state.append_system("[busy] agent is still running; use /abort")
                    self.editor.clear()
                    self._draw()
            return

        submitted = self.editor.handle(key)
        if submitted is None:
            self._draw()
            return
        command = submitted.strip()
        if not command:
            self._draw()
            return
        if is_exit_command(command):
            self.state.mode = "exit"
            return
        if resolve_command(command.split()[0]) is not None:
            # Slash commands are rendered as a normal conversational pair in
            # the viewport; they never share the transient system panel.
            self.state.transcript.append(TranscriptItem("user", command))
            spec = resolve_command(command.split()[0])
            if spec is not None and spec.name == "/resume" and len(command.split()) == 1:
                self._open_resume_overlay()
            elif spec is not None and spec.name == "/drop":
                self._open_drop_overlay(command)
            elif spec is not None and spec.name == "/tree":
                self._open_tree_overlay()
            elif spec is not None and spec.name == "/compact":
                self._start_background_command(command)
            else:
                self._run_command(command)
        else:
            self._start_prompt(command)
        self._draw()

    @staticmethod
    def _mouse_event(key: str) -> tuple[str, int, int] | None:
        for prefix, kind in (("MOUSE_LEFT_DOWN", "down"), ("MOUSE_LEFT_DRAG", "drag"), ("MOUSE_LEFT_UP", "up")):
            if key.startswith(prefix + ":"):
                try:
                    _, x, y = key.split(":", 2)
                    return kind, max(0, int(x)), max(0, int(y))
                except (TypeError, ValueError):
                    return None
        return None

    def _has_selection(self) -> bool:
        anchor, focus = self.state.selection_anchor, self.state.selection_focus
        return bool(anchor and focus and anchor != focus)

    def _is_selector_overlay(self) -> bool:
        return self.state.overlay_kind in {"resume", "tree"} or (
            self.state.overlay_kind == "drop" and not self.state.overlay_value
        )

    def _overlay_item_capacity(self) -> int:
        """Return the number of selector rows available above the fixed footer."""
        kind = self.state.overlay_kind
        fixed = 8 if kind == "resume" else 5 if kind == "drop" else 7
        footer = len(self.renderer.footer_component.render(self.state, self.terminal.columns))
        editor, _ = self.renderer.editor_component.render(self.state, self.terminal.columns)
        return max(1, self.terminal.rows - footer - len(editor) - fixed)

    def _ensure_overlay_visible(self) -> None:
        if not self._is_selector_overlay() or not self.state.overlay_items:
            self.state.overlay_scroll = 0
            return
        capacity = self._overlay_item_capacity()
        maximum = max(0, len(self.state.overlay_items) - capacity)
        start = max(0, min(int(self.state.overlay_scroll), maximum))
        if self.state.overlay_index < start:
            start = self.state.overlay_index
        elif self.state.overlay_index >= start + capacity:
            start = self.state.overlay_index - capacity + 1
        self.state.overlay_scroll = max(0, min(start, maximum))

    def _move_overlay_selection(self, delta: int) -> None:
        if not self.state.overlay_items:
            return
        self.state.overlay_index = max(
            0,
            min(len(self.state.overlay_items) - 1, self.state.overlay_index + int(delta)),
        )
        self._ensure_overlay_visible()

    def _select_overlay_row(self, row: int) -> bool:
        """Map a mouse click to a visible selector row when possible."""
        if row < 0:
            return False
        lines, _ = self.renderer._lines(self.state, apply_selection=False)
        footer = self.renderer.footer_component.render(self.state, self.terminal.columns)
        editor, _ = self.renderer.editor_component.render(self.state, self.terminal.columns)
        body = lines[: max(0, len(lines) - len(footer) - len(editor))]
        # Selector rows are rendered in order and contain the corresponding
        # preview text. The terminal row is already zero-based here.
        if row >= len(body):
            return False
        plain = self.renderer._clip(body[row], self.terminal.columns).strip()
        for index, item in enumerate(self.state.overlay_items):
            visible_index = index - self.state.overlay_scroll
            if 0 <= visible_index < self._overlay_item_capacity() and item and item[: min(24, len(item))] in plain:
                self.state.overlay_index = index
                self._ensure_overlay_visible()
                return True
        return False

    def _copy_selection(self) -> None:
        if not self._has_selection():
            return
        anchor = self.state.selection_anchor
        focus = self.state.selection_focus
        assert anchor is not None and focus is not None
        lines, _ = self.renderer._lines(self.state, apply_selection=False)
        start, end = sorted((anchor, focus), key=lambda point: (point[1], point[0]))
        selected: list[str] = []
        import re

        ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
        for row in range(start[1], end[1] + 1):
            if row < 0 or row >= len(lines):
                continue
            plain = ansi.sub("", lines[row]).rstrip()
            left = start[0] if row == start[1] else 0
            right = end[0] + (1 if row == end[1] else 0)
            selected.append(plain[max(0, left) : max(0, right)].rstrip())
        text = "\n".join(selected).strip("\n")
        if not text:
            self.state.copy_status = "Nothing to copy"
            return
        ok = self.terminal.copy_to_clipboard(text)
        self.state.copy_status = "Copied" if ok else "Copy failed"

    def _open_resume_overlay(self) -> None:
        self.overlay.open_resume()

    def _open_drop_overlay(self, command: str) -> None:
        self.overlay.open_drop(command)

    def _open_tree_overlay(self) -> None:
        self.overlay.open_tree()

    def _handle_overlay_key(self, key: str) -> None:
        self.overlay.handle_key(key)

    def _close_overlay(self, message: str) -> None:
        self.overlay.close(message)

    def _run_command(self, command: str) -> None:
        spec = resolve_command(command.split()[0])
        before_session_id = self.harness.session_id
        before_leaf = getattr(getattr(self.harness, "session_store", None), "current_leaf_id", None)
        output = StringIO()
        self.state.selection_anchor = None
        self.state.selection_focus = None
        self.state.copy_status = None
        handled = handle_repl_command(command, self.harness, output=output)
        self.state.session_id = self.harness.session_id
        self.state.session_name = getattr(self.harness, "session_name", None)
        after_leaf = getattr(getattr(self.harness, "session_store", None), "current_leaf_id", None)
        if before_session_id != self.harness.session_id or before_leaf != after_leaf:
            self._sync_session_view()
            self.state.transcript.append(TranscriptItem("user", command))
        text = output.getvalue().strip()
        if text:
            self.state.transcript.append(TranscriptItem("assistant", text))
        elif not handled:
            self.state.transcript.append(TranscriptItem("assistant", f"unknown command: {command}"))


def run_tui(harness: "Harness", approval_broker: "ApprovalBroker | None" = None) -> int:
    """Run the raw-mode TUI, falling back to line mode when not attached to a TTY."""
    if approval_broker is None:
        from ..repl import ApprovalBroker

        approval_broker = ApprovalBroker()
    return TuiApplication(harness, approval_broker).run()


__all__ = ["TuiApplication", "run_tui"]
