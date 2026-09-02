"""Pure AgentEvent -> UiState reducer."""

from __future__ import annotations

from core.types import AgentEvent
from .state import TranscriptItem, ToolView, UiState


def reduce_event(state: UiState, event: AgentEvent) -> UiState:
    kind = event.kind
    if kind == "request_start":
        # A new model request starts a fresh assistant block. This also
        # protects against late events from a previous worker frame.
        previous = state.assistant_stream
        if previous is not None:
            previous.streaming = False
        state.mode = "working"
        state.status = "working"
        state.turn = int(event.data.get("turn", state.turn or 1))
        state.last_error = None
    elif kind == "text_delta":
        text = str(event.data.get("text", ""))
        if text:
            item = state.assistant_stream
            if item is None:
                item = TranscriptItem("assistant", "", streaming=True)
                state.transcript.append(item)
            item.text += text
    elif kind == "assistant_message":
        item = state.assistant_stream
        if item is not None:
            item.streaming = False
        else:
            message = event.data.get("message")
            content = getattr(message, "content", "")
            if content:
                state.transcript.append(TranscriptItem("assistant", str(content)))
    elif kind == "tool_start":
        state.mode = "working"
        state.status = "tool"
        state.active_tool = ToolView(
            str(event.data.get("name", "tool")),
            event.data.get("arguments", {}),
            str(event.data.get("id", "")),
        )
    elif kind == "tool_result":
        active_tool = state.active_tool
        state.active_tool = None
        state.status = "working"
        result = event.data.get("result")
        content = str(getattr(result, "content", "") or "")
        if content:
            # Keep the complete result in transcript state, but let the TUI
            # render a compact preview by default (Pi expands with Ctrl+O).
            state.transcript.append(
                TranscriptItem(
                    "tool",
                    content,
                    collapsed=True,
                    tool_name=str(getattr(result, "name", "") or getattr(active_tool, "name", "") or ""),
                    tool_error=bool(getattr(result, "is_error", False)),
                    tool_arguments=getattr(active_tool, "arguments", None),
                )
            )
    elif kind == "tool_progress":
        if state.active_tool is not None:
            state.status = "tool"
    elif kind == "usage":
        data = event.data
        state.tokens += int(data.get("tokens", data.get("total_tokens", 0)) or 0)
        state.input_tokens += int(data.get("input_tokens", data.get("input", 0)) or 0)
        if any(key in data for key in ("input_tokens", "input")):
            state.last_input_tokens = int(data.get("input_tokens", data.get("input", 0)) or 0)
        state.output_tokens += int(data.get("output_tokens", data.get("output", 0)) or 0)
        state.cache_read_tokens += int(data.get("cache_read_tokens", data.get("cache_read", 0)) or 0)
        state.cache_write_tokens += int(data.get("cache_write_tokens", data.get("cache_write", 0)) or 0)
        if data.get("cost") is not None:
            try:
                state.cost += float(data["cost"] or 0)
            except (TypeError, ValueError):
                pass
        if data.get("cache_hit_rate") is not None:
            try:
                state.cache_hit_rate = float(data["cache_hit_rate"])
            except (TypeError, ValueError):
                pass
        if data.get("context_percent") is not None:
            try:
                state.context_percent = float(data["context_percent"])
            except (TypeError, ValueError):
                pass
        if data.get("context_window") is not None:
            try:
                state.context_window = int(data["context_window"] or 0)
            except (TypeError, ValueError):
                pass
        # Legacy providers expose only ``tokens``; show it as output too.
        if not any(key in data for key in ("input_tokens", "input", "output_tokens", "output")):
            state.output_tokens += int(data.get("tokens", 0) or 0)
    elif kind == "error":
        message = str(event.data.get("message", "agent error"))
        state.active_tool = None
        state.mode = "error"
        state.status = "error"
        state.last_error = message
        state.append_system(message, error=True)
    elif kind == "compaction_start":
        state.mode = "working"
        state.status = "compacting"
    elif kind == "compaction_end":
        state.status = "error" if event.data.get("is_error") else "working"
    elif kind == "turn_end":
        reason = str(event.data.get("reason", "completed"))
        state.active_tool = None
        state.mode = "error" if reason in {"error", "provider_error", "recovery_exhausted"} else "idle"
        state.status = reason
        item = state.assistant_stream
        if item is not None:
            item.streaming = False
    return state


__all__ = ["reduce_event"]
