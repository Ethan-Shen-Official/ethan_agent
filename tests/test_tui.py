from io import StringIO

from cli.tui import TuiRenderer
from core.types import AgentEvent


class _GbkStream(StringIO):
    encoding = "gbk"


def test_tool_line_is_transient_and_disappears_after_result():
    stream = StringIO()
    ui = TuiRenderer(stream, ansi=False)

    ui.render_event(AgentEvent("tool_start", {"name": "exe", "arguments": {"cmd": "echo ok"}}))
    assert ui.active_tool == 'exe {"cmd":"echo ok"}'
    ui.render_event(AgentEvent("tool_result", {"result": object()}))

    assert ui.active_tool is None
    assert "echo ok" in stream.getvalue()
    # The result payload is intentionally not rendered into the transcript.
    assert "exit_code" not in stream.getvalue()


def test_renderer_degrades_symbols_for_legacy_windows_console_encoding():
    stream = _GbkStream()
    ui = TuiRenderer(stream, ansi=True)
    ui.render_event(AgentEvent("tool_start", {"name": "search", "arguments": {}}))
    ui.render_event(AgentEvent("turn_end", {"reason": "completed"}))
    assert "search" in stream.getvalue()
    assert "completed" in stream.getvalue()


def test_streamed_assistant_text_and_terminal_status_are_rendered():
    stream = StringIO()
    ui = TuiRenderer(stream, ansi=False)

    ui.render_event(AgentEvent("text_delta", {"text": "hello"}))
    ui.render_event(AgentEvent("text_delta", {"text": " world"}))
    ui.render_event(AgentEvent("turn_end", {"reason": "completed"}))

    output = stream.getvalue()
    assert "hello world" in output
    assert "completed" in output
    assert output.endswith("agent> ")


def test_tool_line_is_hidden_before_next_assistant_message():
    stream = StringIO()
    ui = TuiRenderer(stream, ansi=False)

    ui.render_event(AgentEvent("tool_start", {"name": "search", "arguments": {}}))
    ui.render_event(AgentEvent("tool_result", {"result": object()}))
    ui.render_event(AgentEvent("text_delta", {"text": "done"}))

    assert ui.active_tool is None
    assert "done" in stream.getvalue()
