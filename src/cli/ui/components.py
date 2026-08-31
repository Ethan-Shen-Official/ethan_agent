"""Small, composable terminal components used by the Pi-style renderer.

Components only return terminal lines.  They do not write to stdout or own
input, which keeps layout, input dispatch, and agent events independent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .state import ToolView, UiState
from .markdown import render_markdown


class Component(Protocol):
    def render(self, width: int) -> list[str]: ...

    def invalidate(self) -> None: ...


@dataclass
class CallbackComponent:
    callback: Callable[[int], list[str]]

    def render(self, width: int) -> list[str]:
        return list(self.callback(width))

    def invalidate(self) -> None:
        return None


class VStack:
    """Vertical component composition, matching Pi's VStack contract."""

    def __init__(self, children: list[Component] | None = None, *, gap: int = 0) -> None:
        self.children = list(children or [])
        self.gap = max(0, int(gap))

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for index, child in enumerate(self.children):
            if index:
                lines.extend([""] * self.gap)
            lines.extend(child.render(width))
        return lines

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()


class TranscriptComponent:
    """Render ordered user/assistant blocks and transient runtime status."""

    def __init__(self, renderer) -> None:
        self.renderer = renderer

    def render(self, state: UiState, width: int) -> list[str]:
        r = self.renderer
        if state.overlay_kind in {"resume", "tree"} or (state.overlay_kind == "drop" and not state.overlay_value):
            return self._render_selector(state, width)
        content: list[str] = [
            r._styled(r._clip(f" coding-agent  {state.cwd}", width), "36;1"),
            r._styled(r._clip(f" session {state.session_id[:12] or '-'}  {state.status}", width), "2"),
            "",
        ]
        for item in state.transcript:
            if item.kind in {"user", "assistant", "tool"} and content and content[-1] != "":
                content.append("")
            prefix = {"user": "› ", "assistant": "● ", "tool": "● ", "system": "  ", "error": "× "}[item.kind]
            raw_text = item.text or ("…" if item.streaming else "")
            if item.kind == "tool":
                wrapped = self._tool_preview(item, state, width)
            elif item.kind == "assistant":
                wrapped = render_markdown(raw_text, max(1, width - len(prefix) - 2))
            else:
                wrapped = r._wrap(raw_text, max(1, width - len(prefix) - 2)) or [""]
            if item.kind == "user":
                for index, line in enumerate(wrapped):
                    label = prefix if index == 0 else " " * len(prefix)
                    content.append(r._band(label + line, r._USER_FG, r._USER_BG, width))
            elif item.kind == "system":
                for index, line in enumerate(wrapped):
                    label = prefix if index == 0 else " " * len(prefix)
                    content.append(r._band(label + line, r._SYSTEM_FG, r._SYSTEM_BG, width))
            elif item.kind == "error":
                for index, line in enumerate(wrapped):
                    label = prefix if index == 0 else " " * len(prefix)
                    content.append(r._band(label + line, r._ERROR_FG, r._ERROR_BG, width))
            elif item.kind == "tool":
                # Completed tool output is a normal transcript block, but its
                # warm band keeps it distinguishable from model prose.
                for index, line in enumerate(wrapped):
                    label = prefix if index == 0 else " " * len(prefix)
                    content.append(r._band(label + line, r._TOOL_FG, r._TOOL_BG, width))
            else:
                content.append(r._styled(prefix + wrapped[0], ""))
                content.extend("  " + line for line in wrapped[1:])
        if state.active_tool is not None:
            # Tool arguments (especially ``write.content``) can be megabytes
            # long.  Never let them become an unbounded physical terminal
            # line: the footer/editor must remain in their reserved rows.
            running = r._clip(self._tool_call_summary(state.active_tool, r), width)
            if running.startswith("  ◌"):
                frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
                glyph = frames[int(getattr(state, "spinner_frame", 0)) % len(frames)]
                running = f"  {glyph}{running[3:]}"
            content.append(r._styled(running, "2"))
            if state.mode == "permission":
                content.append(r._styled(r._clip("  permission required · press y to allow or n to deny", width), "33"))
        elif state.status == "compacting":
            content.append(r._styled(self._working_indicator(state, "compacting context..."), "2"))
        elif state.mode == "working":
            content.append(r._styled(self._working_indicator(state, "Working..."), "2"))
        if state.overlay_kind == "drop" and state.overlay_value:
            content.append("")
            content.append(r._styled(r._clip(f"  Permanently delete session '{state.overlay_value}'? [y/N]", width), "33;1"))
        return content

    def _render_selector(self, state: UiState, width: int) -> list[str]:
        """Render Pi-style full conversation-area selectors."""
        r = self.renderer
        content: list[str] = []
        if state.overlay_kind == "resume":
            fixed_lines = 8
        elif state.overlay_kind == "drop":
            fixed_lines = 5
        else:
            fixed_lines = 7
        footer = len(r.footer_component.render(state, width))
        editor, _ = r.editor_component.render(state, width)
        capacity = max(1, state.terminal_height - footer - len(editor) - fixed_lines)
        maximum = max(0, len(state.overlay_items) - capacity)
        start = max(0, min(int(getattr(state, "overlay_scroll", 0)), maximum))
        if state.overlay_index < start:
            start = state.overlay_index
        elif state.overlay_index >= start + capacity:
            start = state.overlay_index - capacity + 1
        state.overlay_scroll = max(0, min(start, maximum))
        visible_items = state.overlay_items[state.overlay_scroll : state.overlay_scroll + capacity]

        if state.overlay_kind == "resume":
            content.append(r._styled(r._clip(" Resume Session (Current Folder)", width), "36;1"))
            content.append(r._clip(" ◉ Current Folder | ○ All  Name: All  Sort: Threaded", width))
            content.append(r._styled(r._clip(' tab scope · re:<pattern> regex · "phrase" exact', width), "2"))
            content.append(r._styled(r._clip(" ctrl+s sort · ctrl+n named · ctrl+d delete · ctrl+p path (off) · ctrl+r rename", width), "2"))
            content.append("")
            content.append(r._clip(">", width))
            for local_index, item in enumerate(visible_items):
                index = state.overlay_scroll + local_index
                prefix = "› " if index == state.overlay_index else "  "
                line = r._clip(prefix + item, width)
                if index == state.overlay_index:
                    content.append(r._selection_band(line, width))
                else:
                    role = state.overlay_roles[index] if index < len(state.overlay_roles) else ""
                    content.append(r._styled(line, self._tree_role_style(role)))
            content.append("")
            content.append(r._styled(r._clip("enter resume · up/down move · esc cancel", width), "2"))
            return content

        if state.overlay_kind == "drop":
            content.append(r._styled(r._clip(" Delete Session (Current Folder)", width), "36;1"))
            content.append(r._styled(r._clip(" Current session is protected and hidden", width), "2"))
            content.append("")
            for local_index, item in enumerate(visible_items):
                index = state.overlay_scroll + local_index
                prefix = "› " if index == state.overlay_index else "  "
                line = r._clip(prefix + item, width)
                if index == state.overlay_index:
                    content.append(r._selection_band(line, width))
                else:
                    role = state.overlay_roles[index] if index < len(state.overlay_roles) else ""
                    content.append(r._styled(line, self._tree_role_style(role)))
            content.append("")
            content.append(r._styled(r._clip("enter delete · up/down move · esc cancel", width), "2"))
            return content

        content.append(r._styled(r._clip(" Session Tree", width), "36;1"))
        content.append(r._styled(r._clip(" ↑/↓ move · ←/→ page · ctrl+←/→ branch · ctrl+x copy · esc cancel", width), "2"))
        content.append(r._styled(r._clip(" shift+l label · shift+t label time · filters ctrl+d/t/u/l/a · cycle ctrl+o", width), "2"))
        content.append(r._styled(r._clip(" Type to search:", width), "2"))
        content.append(r._styled("-" * width, "2"))
        for local_index, item in enumerate(visible_items):
            index = state.overlay_scroll + local_index
            prefix = "› " if index == state.overlay_index else "  "
            line = r._clip(prefix + item, width)
            if index == state.overlay_index:
                content.append(r._selection_band(line, width))
            else:
                role = state.overlay_roles[index] if index < len(state.overlay_roles) else ""
                content.append(r._styled(line, self._tree_role_style(role)))
        content.append("")
        content.append(r._styled(r._clip("enter checkout · up/down move · esc cancel", width), "2"))
        return content

    @staticmethod
    def _tool_call_summary(tool: ToolView, renderer) -> str:
        args = tool.arguments if isinstance(tool.arguments, dict) else {}
        name = tool.name
        path = args.get("path", args.get("file_path", ""))
        if name in {"write", "write_file"} and isinstance(args.get("content"), str):
            content = args["content"]
            lines = len(content.splitlines())
            size = len(content.encode("utf-8"))
            return f"  ◌ running {name} {path} ({lines} lines, {size} bytes)"
        if name in {"read", "read_file"} and path:
            suffix = ""
            if args.get("offset") is not None or args.get("max_chars") is not None or args.get("limit") is not None:
                suffix = f" offset={args.get('offset', 1)} limit={args.get('limit', args.get('max_chars', ''))}"
            return f"  ◌ running {name} {path}{suffix}"
        if name in {"edit", "edit_file"} and path:
            edits = args.get("edits")
            count = len(edits) if isinstance(edits, list) else 1
            return f"  ◌ running {name} {path} ({count} edit(s))"
        if name in {"list_dir", "list", "ls"}:
            target = path or "."
            depth = args.get("depth")
            suffix = f" (depth={depth})" if depth is not None else ""
            return f"  ◌ running {name} {target}{suffix}"
        if name in {"search", "grep", "find"}:
            pattern = args.get("pattern", args.get("query", args.get("cmd", "")))
            return f"  ◌ running {name} {str(pattern)}" if pattern else f"  ◌ running {name}"
        if name in {"exe", "shell", "bash", "powershell"}:
            command = str(args.get("cmd", args.get("command", "")))
            return f"  ◌ running {name}: {command}" if command else f"  ◌ running {name}"
        return f"  ◌ running {name}{renderer._arguments(tool.arguments)}"

    def _tool_preview(self, item: TranscriptItem, state: UiState, width: int) -> list[str]:
        available = max(1, width - 4)
        all_lines = render_markdown(item.text, available)
        header = ""
        if item.tool_name and item.tool_arguments is not None:
            header = self._tool_result_summary(item, available)
        if not item.collapsed or state.tools_expanded:
            return ([header] if header else []) + all_lines

        # Pi hides successful read output by default because it is usually
        # only context for the model. Other tools keep a short preview.
        hide_success = item.tool_name in {"read", "read_file"} and not item.tool_error
        shown = [] if hide_success else all_lines[:10]
        remaining = len(all_lines) if hide_success else max(0, len(all_lines) - len(shown))
        if header:
            shown.insert(0, header)
        if remaining:
            noun = "lines hidden" if hide_success else "more lines"
            shown.append(f"... ({remaining} {noun}, Ctrl+O to expand)")
        return shown or ["(no output; Ctrl+O to expand)"]

    def _tool_result_summary(self, item: TranscriptItem, width: int) -> str:
        summary = self._tool_call_summary(ToolView(item.tool_name, item.tool_arguments), self.renderer)
        if summary.startswith("  ◌ running "):
            summary = summary[len("  ◌ running ") :]
        return self.renderer._clip(summary.strip(), width)

    @staticmethod
    def _tree_role_style(role: str) -> str:
        """Apply a Pi-like visual distinction to tree node roles."""
        return {
            "user": "36;1",       # cyan, bold
            "assistant": "97;1",  # bright white, bold
            "tool": "33;1",       # yellow, bold
            "system": "2",
            "session_info": "2",
        }.get(role, "")

    @staticmethod
    def _working_indicator(state: UiState, label: str) -> str:
        """Return the current braille spinner frame and status label."""
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        glyph = frames[int(getattr(state, "spinner_frame", 0)) % len(frames)]
        return f"  {glyph} {label}"

    def invalidate(self) -> None:
        return None


class FooterComponent:
    def __init__(self, renderer) -> None:
        self.renderer = renderer

    def render(self, state: UiState, width: int) -> list[str]:
        return self.renderer._footer_impl(state, width)

    def invalidate(self) -> None:
        return None


class EditorComponent:
    def __init__(self, renderer) -> None:
        self.renderer = renderer

    def render(self, state: UiState, width: int) -> tuple[list[str], tuple[int, int]]:
        return self.renderer._editor_lines_impl(state, width)

    def invalidate(self) -> None:
        return None


__all__ = ["CallbackComponent", "Component", "EditorComponent", "FooterComponent", "TranscriptComponent", "VStack"]
