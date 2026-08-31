"""Terminal rendering helpers and the Pi-inspired viewport renderer."""

from __future__ import annotations

import json
import os
import re
import unicodedata

from core.types import AgentEvent
from .components import EditorComponent, FooterComponent, TranscriptComponent
from .state import UiState

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_terminal_text(value: object) -> str:
    """Remove terminal protocol bytes before text enters the layout tree."""
    text = str(value or "").replace("\r", "")
    text = _OSC_RE.sub("", text)
    text = _ANSI_RE.sub("", text)
    return _CONTROL_RE.sub("", text)


def render(event: AgentEvent) -> None:
    """Compatibility line renderer used by one-shot (non-interactive) mode."""
    if event.kind == "text_delta":
        print(event.data.get("text", ""), end="", flush=True)
    elif event.kind == "tool_start":
        print(f"\n[tool] {event.data['name']} {event.data['arguments']}")
    elif event.kind == "tool_result":
        result = event.data["result"]
        print(f"[result] {result.content}")
    elif event.kind == "turn_end":
        print(f"\n[{event.data['reason']}]")
    elif event.kind == "error":
        print(f"\n[error] {event.data['message']}")
    elif event.kind == "compaction_start":
        print("\n[compaction] summarizing context...", flush=True)
    elif event.kind == "compaction_end":
        if event.data.get("is_error"):
            print(f"[compaction error] {event.data.get('error', 'unknown error')}")
        else:
            print("[compaction] context summary saved")


def format_tokens(count: int) -> str:
    """Use Pi's compact token notation."""
    count = max(0, int(count or 0))
    if count < 1000:
        return str(count)
    if count < 10000:
        return f"{count / 1000:.1f}k"
    if count < 1_000_000:
        return f"{round(count / 1000):.0f}k"
    if count < 10_000_000:
        return f"{count / 1_000_000:.1f}M"
    return f"{round(count / 1_000_000):.0f}M"


class ScreenRenderer:
    """Render a complete Pi-style viewport from :class:`UiState`.

    Full redraws are deliberate for the first TUI implementation. Every
    frame is assembled before it is written, so async events cannot interleave
    with the editor or leave stale tool output behind.
    """

    _USER_FG, _USER_BG = "97", "48;5;24"
    _SYSTEM_FG, _SYSTEM_BG = "37", "48;5;236"
    _TOOL_FG, _TOOL_BG = "97", "48;5;58"
    _ERROR_FG, _ERROR_BG = "97", "48;5;52"

    def __init__(self, terminal) -> None:
        self.terminal = terminal
        self.transcript_component = TranscriptComponent(self)
        self.footer_component = FooterComponent(self)
        self.editor_component = EditorComponent(self)
        self._previous_lines: list[str] = []
        self._previous_size: tuple[int, int] | None = None

    def invalidate(self) -> None:
        """Discard the differential frame cache after terminal ownership changes."""
        self._previous_lines = []
        self._previous_size = None

    def render(self, state: UiState) -> None:
        state.terminal_width = self.terminal.columns
        state.terminal_height = self.terminal.rows
        lines, cursor = self._lines(state)
        if self.terminal.is_tty:
            size = (state.terminal_width, state.terminal_height)
            full = not self._previous_lines or self._previous_size != size
            # Synchronized output prevents a partially drawn frame from
            # exposing stale rows while async model/tool events arrive.
            self.terminal.write("\x1b[?2026h\x1b[?25l")
            if full:
                self.terminal.write("\x1b[2J\x1b[3J\x1b[H")
                self.terminal.write("\r\n".join(lines))
            else:
                for row, line in enumerate(lines):
                    if row >= len(self._previous_lines) or line != self._previous_lines[row]:
                        self.terminal.write(f"\x1b[{row + 1};1H\x1b[2K{line}")
                for row in range(len(lines), len(self._previous_lines)):
                    self.terminal.write(f"\x1b[{row + 1};1H\x1b[2K")
            self.terminal.write(f"\x1b[{cursor[0]};{cursor[1]}H\x1b[?25h")
            self.terminal.write("\x1b[?2026l")
            self._previous_lines = list(lines)
            self._previous_size = size
        else:
            self.terminal.write("\n".join(lines) + "\n")

    def _lines(self, state: UiState, *, apply_selection: bool = True) -> tuple[list[str], tuple[int, int]]:
        width = max(20, int(getattr(self.terminal, "columns", state.terminal_width)))
        height = max(8, int(getattr(self.terminal, "rows", state.terminal_height)))
        content = self.transcript_component.render(state, width)

        footer = self.footer_component.render(state, width)
        editor, cursor = self.editor_component.render(state, width)
        available_body = max(0, height - len(footer) - len(editor))
        if available_body:
            offset = max(0, min(int(state.scroll_offset), max(0, len(content) - available_body)))
            end = len(content) - offset
            body = content[max(0, end - available_body) : end]
        else:
            body = []
        if apply_selection and state.selection_anchor and state.selection_focus and self.terminal.is_tty:
            body = self._highlight_selection(body, state.selection_anchor, state.selection_focus, width)
        lines = body + footer + editor
        return lines, (len(body) + len(footer) + cursor[0] + 1, cursor[1] + 1)

    def _highlight_selection(
        self,
        lines: list[str],
        anchor: tuple[int, int],
        focus: tuple[int, int],
        width: int,
    ) -> list[str]:
        """Apply reverse video to the selected terminal cells."""
        start, end = sorted((anchor, focus), key=lambda point: (point[1], point[0]))
        highlighted: list[str] = []
        for row, line in enumerate(lines):
            if row < start[1] or row > end[1]:
                highlighted.append(line)
                continue
            plain = self._clip(clean_terminal_text(line), width)
            left = start[0] if row == start[1] else 0
            right = end[0] + (1 if row == end[1] else width)
            left = max(0, min(left, len(plain)))
            right = max(left, min(right, len(plain)))
            if left == right:
                highlighted.append(line)
                continue
            selected = plain[left:right]
            highlighted.append(plain[:left] + "\x1b[7m" + selected + "\x1b[27m" + plain[right:])
        return highlighted

    def _footer_impl(self, state: UiState, width: int) -> list[str]:
        cwd = self._short_cwd(state.cwd)
        if state.session_name:
            cwd = f"{cwd} • {state.session_name}"
        if state.copy_status:
            cwd = f"{cwd} • {state.copy_status}"
        stats: list[str] = []
        if state.input_tokens:
            stats.append(f"↑{format_tokens(state.input_tokens)}")
        if state.output_tokens:
            stats.append(f"↓{format_tokens(state.output_tokens)}")
        if state.cache_read_tokens:
            stats.append(f"R{format_tokens(state.cache_read_tokens)}")
        if state.cache_write_tokens:
            stats.append(f"W{format_tokens(state.cache_write_tokens)}")
        if state.cache_hit_rate is not None:
            stats.append(f"CH{state.cache_hit_rate:.1f}%")
        if state.cost:
            stats.append(f"${state.cost:.3f}")
        if state.context_window:
            pct = "?" if state.context_percent is None else f"{state.context_percent:.1f}%"
            stats.append(f"{pct}/{format_tokens(state.context_window)}" + (" (auto)" if state.auto_compact else ""))
        elif state.tokens and not stats:
            stats.append(f"↓{format_tokens(state.tokens)}")
        if not stats:
            stats.append(f"turn {state.turn} | {state.status}")
        left = " ".join(stats)
        right = state.model_name
        if state.thinking_level:
            right = f"{right} • {state.thinking_level}" if right else state.thinking_level
        if right:
            right_width = self._display_width(right)
            left = self._clip(left, max(1, width - right_width - 3))
            line = f" {left}{' ' * max(2, width - self._display_width(left) - right_width - 1)}{right}"
        else:
            line = f" {left}"
        # Keep the status region visually separated from the transcript while
        # reserving it as a fixed block at the bottom of the viewport.
        separator = self._styled("-" * width, "2")
        return [separator, self._styled(self._clip(f" {cwd}", width), "2"), self._styled(self._clip(line, width), "2")]

    # Compatibility helpers retained for callers that used the early P0
    # renderer directly; layout now goes through FooterComponent.
    def _footer(self, state: UiState, width: int) -> list[str]:
        return self.footer_component.render(state, width)

    def _editor_lines_impl(self, state: UiState, width: int) -> tuple[list[str], tuple[int, int]]:
        prompt = " select> " if state.overlay_kind == "resume" else " confirm> " if state.overlay_kind == "drop" else " agent> "
        prompt_width = self._display_width(prompt)
        available = max(1, width - prompt_width)
        raw_lines = state.input_text.split("\n") or [""]
        rendered: list[str] = []
        absolute = max(0, min(len(state.input_text), state.cursor_position))
        before = state.input_text[:absolute]
        cursor_row = 0
        cursor_col = len(prompt)
        for row, value in enumerate(raw_lines):
            chunks: list[str] = []
            remaining = value
            while remaining:
                chunk = self._take_cells(remaining, available)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining = remaining[len(chunk) :]
            chunks = chunks or [""]
            for chunk_index, chunk in enumerate(chunks):
                label = prompt if row == 0 and chunk_index == 0 else " " * prompt_width
                rendered.append(self._band(label + chunk, self._USER_FG, self._USER_BG, width))
        # Map the logical cursor offset to the wrapped display row. Newlines
        # and terminal-width wrapping both consume a row.
        logical_lines = before.split("\n")
        cursor_row = 0
        for index, value in enumerate(logical_lines):
            cursor_row += max(1, (self._display_width(value) + available - 1) // available)
            if index < len(logical_lines) - 1:
                continue
            cursor_row -= 1
            cursor_col = prompt_width + (self._display_width(value) % available)
            if value and self._display_width(value) % available == 0:
                cursor_col = prompt_width + available
        cursor_row = min(max(0, len(rendered) - 1), cursor_row)
        return rendered, (cursor_row, min(width - 1, cursor_col))

    def _editor_lines(self, state: UiState, width: int) -> tuple[list[str], tuple[int, int]]:
        return self.editor_component.render(state, width)

    def _short_cwd(self, cwd: str) -> str:
        home = os.path.expanduser("~")
        try:
            absolute = os.path.abspath(cwd)
            home_absolute = os.path.abspath(home)
            if os.path.normcase(absolute) == os.path.normcase(home_absolute):
                return "~"
            if os.path.normcase(absolute).startswith(os.path.normcase(home_absolute + os.sep)):
                return "~" + absolute[len(home_absolute) :]
        except (OSError, ValueError):
            pass
        return cwd

    @staticmethod
    def _clip(text: str, width: int) -> str:
        clean = clean_terminal_text(text).replace("\n", " ")
        if ScreenRenderer._display_width(clean) <= width:
            return clean
        # Keep clipping ASCII-safe.  A number of Windows consoles still
        # expose a legacy code page even when ANSI is supported.
        if width < 3:
            return ScreenRenderer._take_cells(clean, width)
        return ScreenRenderer._take_cells(clean, width - 3) + "..."

    def _band(self, text: str, fg: str, bg: str, width: int) -> str:
        plain = self._clip(text, width)
        return self._styled(plain + " " * max(0, width - self._display_width(plain)), f"{bg};{fg}")

    def _selection_band(self, text: str, width: int) -> str:
        """Highlight the focused selector row without changing its width."""
        plain = self._clip(text, width)
        padded = plain + " " * max(0, width - self._display_width(plain))
        return self._styled(padded, "7")

    def _styled(self, text: str, code: str) -> str:
        if not code or not self.terminal.is_tty:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        clean = clean_terminal_text(text)
        result: list[str] = []
        for paragraph in clean.split("\n"):
            if not paragraph:
                result.append("")
                continue
            while ScreenRenderer._display_width(paragraph) > width:
                chunk = ScreenRenderer._take_cells(paragraph, width)
                result.append(chunk)
                paragraph = paragraph[len(chunk) :]
            result.append(paragraph)
        return result

    @staticmethod
    def _char_width(char: str) -> int:
        if unicodedata.combining(char):
            return 0
        return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1

    @classmethod
    def _display_width(cls, text: str) -> int:
        return sum(cls._char_width(char) for char in str(text))

    @classmethod
    def _take_cells(cls, text: str, width: int) -> str:
        result: list[str] = []
        used = 0
        for char in str(text):
            char_width = cls._char_width(char)
            if used + char_width > width:
                break
            result.append(char)
            used += char_width
        return "".join(result)

    @staticmethod
    def _arguments(arguments: object) -> str:
        try:
            value = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            value = str(arguments)
        return "" if value in {"", "{}"} else f" {value.replace(chr(10), ' ')}"


__all__ = ["ScreenRenderer", "clean_terminal_text", "format_tokens", "render"]
