"""Structured state consumed by the terminal UI renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


UiMode = Literal["idle", "working", "permission", "error", "exit"]
OverlayKind = Literal["resume", "tree", "drop"]


@dataclass
class TranscriptItem:
    kind: Literal["user", "assistant", "tool", "system", "error"]
    text: str
    streaming: bool = False
    # Tool results remain available for expansion without forcing the full
    # payload into the default viewport.
    collapsed: bool = False
    tool_name: str = ""
    tool_error: bool = False
    # Arguments are retained only for the transient UI projection so a
    # collapsed result can still identify its file/command. They are not
    # serialized as part of the session transcript.
    tool_arguments: object = None


@dataclass
class ToolView:
    name: str
    arguments: object
    call_id: str = ""


@dataclass
class UiState:
    transcript: list[TranscriptItem] = field(default_factory=list)
    input_text: str = ""
    cursor_position: int = 0
    mode: UiMode = "idle"
    status: str = "ready"
    # Monotonic frame index used by the main-thread spinner.  Keeping this
    # in state makes animation deterministic in tests and prevents worker
    # threads from writing to the terminal concurrently with rendering.
    spinner_frame: int = 0
    active_tool: ToolView | None = None
    turn: int = 0
    # ``tokens`` remains the aggregate counter used by older providers. The
    # detailed counters are optional and power the Pi-style footer when a
    # provider supplies them.
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0
    cache_hit_rate: float | None = None
    context_percent: float | None = None
    context_window: int = 0
    auto_compact: bool = True
    model_name: str = ""
    thinking_level: str = ""
    permission_mode: str = "ask"
    session_name: str | None = None
    last_error: str | None = None
    terminal_width: int = 80
    terminal_height: int = 24
    # Number of content rows scrolled up from the live bottom viewport.
    scroll_offset: int = 0
    # Terminal-cell coordinates (zero based) for the application-owned text
    # selection.  Keeping these in UI state lets the renderer highlight the
    # range without mutating the transcript itself.
    selection_anchor: tuple[int, int] | None = None
    selection_focus: tuple[int, int] | None = None
    copy_status: str | None = None
    cwd: str = "."
    session_id: str = ""
    # One-shot Pi-style onboarding shown before the first submitted message.
    # Resource names are populated by the application, keeping components
    # pure and avoiding filesystem access during rendering.
    startup_context: tuple[str, ...] = ()
    # Interactive slash-command overlays stay inside the managed TUI.  They
    # are intentionally data-only so the renderer can keep the transcript and
    # fixed footer alive while a selector/confirmation is active.
    overlay_kind: OverlayKind | None = None
    overlay_title: str = ""
    overlay_items: list[str] = field(default_factory=list)
    # Optional semantic role for each selector item.  Tree overlays use this
    # to apply Pi-like role-specific emphasis while resume/drop keep it empty.
    overlay_roles: list[str] = field(default_factory=list)
    overlay_ids: list[str] = field(default_factory=list)
    overlay_index: int = 0
    # First visible item in a long selector (resume/drop/tree).
    overlay_scroll: int = 0
    overlay_value: str = ""
    tools_expanded: bool = False

    @property
    def assistant_stream(self) -> TranscriptItem | None:
        if self.transcript and self.transcript[-1].kind == "assistant" and self.transcript[-1].streaming:
            return self.transcript[-1]
        return None

    def append_system(self, text: str, *, error: bool = False) -> None:
        if text.strip():
            self.transcript.append(TranscriptItem("error" if error else "system", text.rstrip()))


__all__ = ["OverlayKind", "TranscriptItem", "ToolView", "UiMode", "UiState"]
