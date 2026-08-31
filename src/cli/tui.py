"""Small terminal UI for the interactive coding-agent session.

The renderer deliberately owns only terminal presentation.  Agent execution,
commands, permissions, and session state remain in the existing Harness and
CLI modules.  A stable transcript is written once; tool calls live in a
single redrawable status line and disappear when their result arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import sys
import threading
from typing import TextIO

from core.types import AgentEvent


@dataclass(frozen=True)
class TuiTheme:
    """ANSI palette used by the default Pi-like presentation."""

    cyan: str = "\x1b[36m"
    yellow: str = "\x1b[33m"
    green: str = "\x1b[32m"
    red: str = "\x1b[31m"
    dim: str = "\x1b[2m"
    bold: str = "\x1b[1m"
    reset: str = "\x1b[0m"


class TuiRenderer:
    """Stateful, thread-safe renderer for the interactive agent UI.

    ``tool_start`` creates one transient line. ``tool_result`` removes it
    instead of appending command arguments and output to the transcript. This
    keeps the conversation readable while retaining a visible progress cue.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        ansi: bool | None = None,
        theme: TuiTheme | None = None,
        prompt: str = "agent> ",
        interactive: bool = True,
    ) -> None:
        self.stream = stream or sys.stdout
        self.ansi = self.stream.isatty() if ansi is None else ansi
        self.theme = theme or TuiTheme()
        self.prompt = prompt
        self.interactive = interactive
        self._lock = threading.RLock()
        self._live_tool: str | None = None
        self._assistant_open = False
        self._started = False
        self._last_reason: str | None = None

    def start(self, *, cwd: str | None = None, session_id: str | None = None) -> None:
        """Render a compact session header once at REPL startup."""
        with self._lock:
            if self._started:
                return
            self._started = True
            title = self._style("coding-agent", "bold")
            details: list[str] = []
            if cwd:
                details.append(cwd)
            if session_id:
                details.append(f"session {session_id[:12]}")
            suffix = f"  {self._style(' | '.join(details), 'dim')}" if details else ""
            self._write(f"{title}{suffix}\n")

    def read_prompt(self) -> str:
        """Read one line through the UI-owned prompt."""
        with self._lock:
            # ``input`` handles line editing while the renderer handles all
            # asynchronous redraws.  The prompt itself is redrawn after each
            # AgentEvent, so it cannot remain hidden behind streamed output.
            return input(self.prompt)

    def render_event(self, event: AgentEvent) -> None:
        with self._lock:
            kind = event.kind
            if kind == "text_delta":
                self._render_text_delta(str(event.data.get("text", "")))
            elif kind == "tool_start":
                self._render_tool_start(event)
            elif kind == "tool_progress":
                if self._live_tool is not None:
                    self._render_live(self._live_tool)
            elif kind == "tool_result":
                self._hide_live_tool()
            elif kind == "assistant_message":
                # Text deltas already formed the visible assistant message.
                # Keep this event for stateful consumers without duplicating it.
                pass
            elif kind == "turn_end":
                self._hide_live_tool()
                self._assistant_open = False
                reason = str(event.data.get("reason", "completed"))
                self._last_reason = reason
                self._write(f"\n{self._status(reason)}\n")
                if self.interactive:
                    self.redraw_prompt()
            elif kind == "error":
                self._hide_live_tool()
                self._write(f"\n{self._style('error', 'red')}: {event.data.get('message', '')}\n")
                if self.interactive:
                    self.redraw_prompt()
            elif kind == "compaction_start":
                self._hide_live_tool()
                self._write(f"\n{self._style('summarizing context...', 'dim')}\n")
            elif kind == "compaction_end":
                if event.data.get("is_error"):
                    self._write(f"{self._style('compaction error', 'red')}: {event.data.get('error', '')}\n")
                else:
                    self._write(f"{self._style('context summary saved', 'dim')}\n")
                self.redraw_prompt()

    def render_system(self, text: str) -> None:
        """Append command/help output as a stable system line."""
        with self._lock:
            self._hide_live_tool()
            self._write(f"\n{text.rstrip()}\n")
            if self.interactive:
                self.redraw_prompt()

    def redraw_prompt(self) -> None:
        """Redraw the bottom prompt after asynchronous output."""
        with self._lock:
            if self.ansi:
                self._write(f"\r\x1b[2K{self.prompt}")
            else:
                # In redirected output there is no cursor to preserve. A
                # newline makes the prompt unambiguous for logs and tests.
                self._write(self.prompt)

    def close(self) -> None:
        with self._lock:
            self._hide_live_tool()
            if self.ansi:
                self._write("\n")

    @property
    def active_tool(self) -> str | None:
        return self._live_tool

    @property
    def last_reason(self) -> str | None:
        return self._last_reason

    def _render_text_delta(self, text: str) -> None:
        if not text:
            return
        if self._live_tool is not None:
            self._hide_live_tool()
        if not self._assistant_open:
            self._write(f"\n{self._style('●', 'cyan')} ")
            self._assistant_open = True
        self._write(text)

    def _render_tool_start(self, event: AgentEvent) -> None:
        self._hide_live_tool()
        name = str(event.data.get("name", "tool"))
        arguments = event.data.get("arguments", {})
        self._live_tool = self._format_tool(name, arguments)
        self._render_live(self._live_tool)

    def _render_live(self, text: str) -> None:
        if self.ansi:
            self._write(f"\r\x1b[2K  {self._style('◌', 'yellow')} {text}")
        else:
            self._write(f"\r  {text}")

    def _hide_live_tool(self) -> None:
        if self._live_tool is None:
            return
        if self.ansi:
            self._write("\r\x1b[2K")
        else:
            self._write("\r")
        self._live_tool = None

    def _status(self, reason: str) -> str:
        if reason == "completed":
            return self._style("✓ completed", "green")
        if reason == "cancelled":
            return self._style("■ cancelled", "yellow")
        if reason in {"provider_error", "recovery_exhausted"}:
            return self._style(f"× {reason}", "red")
        return self._style(f"· {reason}", "dim")

    def _format_tool(self, name: str, arguments: object) -> str:
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            encoded = str(arguments)
        width = max(40, shutil.get_terminal_size(fallback=(100, 24)).columns - 8)
        text = f"{name} {encoded}" if encoded not in {"{}", ""} else name
        text = text.replace("\r", " ").replace("\n", " ")
        return text if len(text) <= width else text[: width - 1] + "…"

    def _style(self, text: str, color: str) -> str:
        if not self.ansi:
            return text
        code = getattr(self.theme, color)
        return f"{code}{text}{self.theme.reset}"

    def _write(self, text: str) -> None:
        encoding = getattr(self.stream, "encoding", None)
        if encoding:
            text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        self.stream.write(text)
        self.stream.flush()


__all__ = ["TuiRenderer", "TuiTheme"]
