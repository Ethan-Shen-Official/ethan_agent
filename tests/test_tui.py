from io import StringIO

from cli.input import InputEditor
from cli.reducer import reduce_event
from cli.renderer import ScreenRenderer
from cli.state import TranscriptItem, ToolView, UiState
from cli.terminal import TerminalBackend
from cli.markdown import render_markdown
from cli.tui import TuiApplication
from cli.repl import ApprovalBroker
from core.types import AgentEvent


class _FakeTerminal:
    is_tty = False
    columns = 60
    rows = 14

    def __init__(self):
        self.output = StringIO()

    def write(self, value):
        self.output.write(value)


def test_input_editor_supports_cursor_editing_and_history():
    editor = InputEditor()
    editor.handle("a")
    editor.handle("b")
    editor.handle("LEFT")
    editor.handle("x")
    assert editor.text == "axb"
    assert editor.cursor == 2
    assert editor.handle("\r") == "axb"
    editor.handle("UP")
    assert editor.text == "axb"
    editor.handle("SHIFT_ENTER")
    editor.handle("z")
    assert editor.text == "axb\nz"


def test_input_editor_inserts_bracketed_paste_without_submitting():
    editor = InputEditor()
    editor.insert_text("first\nsecond")
    assert editor.text == "first\nsecond"
    assert editor.cursor == len("first\nsecond")


def test_markdown_renderer_normalizes_common_agent_reply_syntax():
    lines = render_markdown("# Title\n\n- **done**\n> note\n```\ncode\n```", 20)
    assert lines[:4] == ["▌ Title", "", "• done", "│ note"]
    assert "  code" in lines
    assert all(len(line) <= 20 for line in lines)


def test_markdown_renderer_unescapes_markup_and_wraps_wide_text_by_cells():
    assert render_markdown(r"- \`literal\`", 40) == ["• literal"]
    assert render_markdown(r"Note: \`", 40) == ["Note: `"]
    assert render_markdown("中文测试", 6) == ["中文测", "试"]


def test_renderer_keeps_all_runtime_command_output_blocks():
    terminal = _FakeTerminal()
    terminal.rows = 30
    state = UiState(cwd="D:/workspace")
    state.transcript = [
        TranscriptItem("system", "first command output"),
        TranscriptItem("system", "second command output"),
    ]
    text = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "first command output" in text
    assert "second command output" in text


def test_reducer_tracks_tool_as_transient_operation():
    state = UiState()
    reduce_event(state, AgentEvent("request_start", {"turn": 2}))
    reduce_event(state, AgentEvent("tool_start", {"id": "c1", "name": "search", "arguments": {}}))
    assert state.mode == "working"
    assert state.active_tool is not None
    reduce_event(state, AgentEvent("tool_result", {"result": object()}))
    assert state.active_tool is None
    assert state.transcript == []
    from core.types import ToolResult

    reduce_event(state, AgentEvent("tool_result", {"result": ToolResult("c1", "search", "result text")}))
    assert [(item.kind, item.text) for item in state.transcript] == [("tool", "result text")]


def test_reducer_accumulates_detailed_usage_for_footer():
    state = UiState()
    reduce_event(
        state,
        AgentEvent(
            "usage",
            {
                "tokens": 7073,
                "input_tokens": 6300,
                "output_tokens": 773,
                "cache_read_tokens": 37000,
                "cache_hit_rate": 96.9,
                "cost": 0.004,
                "context_percent": 0.6,
                "context_window": 1_000_000,
            },
        ),
    )
    assert (state.input_tokens, state.output_tokens, state.cache_read_tokens) == (6300, 773, 37000)
    assert state.cost == 0.004
    assert state.context_window == 1_000_000


def test_screen_renderer_reserves_editor_and_footer_and_hides_completed_tool():
    terminal = _FakeTerminal()
    renderer = ScreenRenderer(terminal)
    state = UiState(cwd="D:/workspace", session_id="abcdef123456", input_text="hello", cursor_position=5)
    state.append_system("ready")
    renderer.render(state)
    output = terminal.output.getvalue()
    assert "D:/workspace" in output
    assert "agent> hello" in output
    assert "turn 0" in output
    assert "\x1b[" not in output

    state.active_tool = None
    terminal.output = StringIO()
    renderer.render(state)
    assert "agent> hello" in terminal.output.getvalue()


def test_non_tty_terminal_does_not_enable_raw_mode():
    class Pipe:
        encoding = "utf-8"

        def isatty(self):
            return False

    terminal = TerminalBackend(Pipe(), Pipe())
    assert terminal.is_tty is False
    terminal.start()
    assert terminal.read_key(0) is None
    terminal.stop()


def test_tui_syncs_editor_text_into_rendered_state():
    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.editor.handle("a")
    app.editor.handle("b")
    app._draw()
    assert app.state.input_text == "ab"
    assert app.state.cursor_position == 2
    assert "agent> ab" in terminal.output.getvalue()


def test_tui_restores_user_and_assistant_history_without_tool_rows():
    from core.state import LoopState
    from core.types import Message

    class HarnessStub:
        is_running = False
        session_id = "session-id"
        state = LoopState(messages=[Message.user("old question"), Message.assistant("old answer")])

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    assert [(item.kind, item.text) for item in app.state.transcript] == [
        ("user", "old question"),
        ("assistant", "old answer"),
    ]


def test_tui_restores_tool_history_in_collapsed_form():
    from core.state import LoopState
    from core.types import Message, ToolCall, ToolResult

    class HarnessStub:
        is_running = False
        session_id = "session-id"
        state = LoopState(
            messages=[
                Message.user("inspect the file"),
                Message.assistant(tool_calls=[ToolCall("c1", "read_file", {"path": "large.txt"})]),
                Message.tool(ToolResult("c1", "read_file", "\n".join(f"line {i}" for i in range(30)))),
            ]
        )

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    tool = app.state.transcript[-1]
    assert tool.kind == "tool"
    assert tool.collapsed is True
    assert tool.tool_name == "read_file"
    assert tool.tool_arguments == {"path": "large.txt"}
    assert tool.tool_error is False


def test_tty_renderer_uses_pi_style_message_bands_and_footer_stats():
    class TtyTerminal(_FakeTerminal):
        is_tty = True
        columns = 100
        rows = 14

    terminal = TtyTerminal()
    state = UiState(
        cwd="D:/workspace",
        session_id="abcdef123456",
        input_text="hello",
        cursor_position=5,
        input_tokens=6300,
        output_tokens=773,
        tokens=7073,
        cache_read_tokens=37000,
        cache_hit_rate=96.9,
        cost=0.004,
        context_percent=0.6,
        context_window=1_000_000,
        model_name="deepseek-v4-pro",
        thinking_level="high",
    )
    # Keep construction explicit so this test does not depend on reducer details.
    from cli.state import TranscriptItem, ToolView

    state.transcript = [TranscriptItem("user", "hello"), TranscriptItem("system", "command output")]
    state.active_tool = ToolView("read", {"path": "a.txt"})
    ScreenRenderer(terminal).render(state)
    output = terminal.output.getvalue()
    assert "48;5;24" in output  # user/input background
    assert "48;5;236" in output  # console background
    assert "running read" in output
    assert "↑6.3k" in output
    assert "↓773" in output
    assert "R37k" in output
    assert "CH96.9%" in output
    assert "$0.004" in output
    assert "0.6%/1.0M (auto)" in output
    assert "deepseek-v4-pro" in output
    assert output.count("↓8.3k") == 0


def test_renderer_clips_background_rows_to_terminal_width():
    class TtyTerminal(_FakeTerminal):
        is_tty = True
        columns = 24
        rows = 8

    terminal = TtyTerminal()
    state = UiState(cwd="D:/very/long/workspace", input_text="x" * 100, cursor_position=100)
    from cli.state import TranscriptItem, ToolView

    state.transcript.append(TranscriptItem("user", "y" * 100))
    state.active_tool = ToolView("write", {"path": "large.cpp", "content": "x" * 10000})
    renderer = ScreenRenderer(terminal)
    lines, _ = renderer._lines(state)
    import re

    ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    assert all(len(ansi.sub("", line)) <= terminal.columns for line in lines)


def test_tool_output_is_collapsed_and_expands_with_ctrl_o():
    terminal = _FakeTerminal()
    terminal.rows = 100
    state = UiState(cwd="D:/workspace")
    state.transcript = [
        TranscriptItem(
            "tool",
            "\n".join(f"line {index}" for index in range(30)),
            collapsed=True,
            tool_name="read_file",
            tool_arguments={"path": "large.txt"},
        )
    ]
    renderer = ScreenRenderer(terminal)
    collapsed, _ = renderer._lines(state)
    collapsed_text = "\n".join(collapsed)
    assert "line 29" not in collapsed_text
    assert "lines hidden" in collapsed_text
    assert "read_file large.txt" in collapsed_text
    state.tools_expanded = True
    expanded, _ = renderer._lines(state)
    assert "line 29" in "\n".join(expanded)


def test_ctrl_o_toggles_global_tool_expansion():
    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    assert app.state.tools_expanded is False
    app._handle_key("\x0f")
    assert app.state.tools_expanded is True
    app._handle_key("\x0f")
    assert app.state.tools_expanded is False


def test_large_write_tool_call_renders_summary_not_file_content():
    terminal = _FakeTerminal()
    state = UiState(cwd="D:/workspace", active_tool=ToolView("write", {"path": "simple_fs.cpp", "content": "x\n" * 500}))
    lines, _ = ScreenRenderer(terminal)._lines(state)
    text = "\n".join(lines)
    assert "simple_fs.cpp" in text
    assert "500 lines" in text
    assert "x\nx" not in text


def test_list_dir_running_summary_does_not_dump_partial_json():
    terminal = _FakeTerminal()
    state = UiState(cwd="D:/workspace", active_tool=ToolView("list_dir", {"path": ".", "depth": 2}))
    text = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "running list_dir . (depth=2)" in text
    assert '{"path"' not in text


def test_completed_read_result_keeps_compact_file_header():
    terminal = _FakeTerminal()
    state = UiState(cwd="D:/workspace")
    state.transcript = [
        TranscriptItem(
            "tool",
            "\n".join(f"line {index}" for index in range(30)),
            collapsed=True,
            tool_name="read_file",
            tool_arguments={"path": "src/main.py"},
        )
    ]
    text = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "read_file src/main.py" in text
    assert "line 29" not in text


def test_renderer_scroll_offset_moves_only_conversation_viewport():
    terminal = _FakeTerminal()
    terminal.rows = 8
    state = UiState(cwd="D:/workspace")
    from cli.state import TranscriptItem

    state.transcript = [
        item
        for index in range(8)
        for item in (TranscriptItem("user", f"question {index}"), TranscriptItem("assistant", f"answer {index}"))
    ]
    renderer = ScreenRenderer(terminal)
    live, _ = renderer._lines(state)
    state.scroll_offset = 100
    scrolled, _ = renderer._lines(state)
    assert live[-1].startswith(" agent>")
    assert scrolled[-1].startswith(" agent>")
    assert live != scrolled


def test_terminal_decodes_sgr_mouse_wheel_sequences():
    assert TerminalBackend._decode_escape("\x1b[<64;10;5M") == "MOUSE_WHEEL_UP"
    assert TerminalBackend._decode_escape("\x1b[<65;10;5M") == "MOUSE_WHEEL_DOWN"


def test_terminal_decodes_sgr_mouse_selection_sequences():
    assert TerminalBackend._decode_escape("\x1b[<0;5;2M") == "MOUSE_LEFT_DOWN:4:1"
    assert TerminalBackend._decode_escape("\x1b[<32;8;3M") == "MOUSE_LEFT_DRAG:7:2"
    assert TerminalBackend._decode_escape("\x1b[<0;8;3m") == "MOUSE_LEFT_UP:7:2"
    assert TerminalBackend._decode_escape("\x1b[<badM") == "MOUSE_IGNORED"
    assert TerminalBackend._decode_escape("\x1b[200~") == "PASTE_START"
    assert TerminalBackend._decode_escape("\x1b[201~") == "PASTE_END"


def test_tui_mouse_selection_copies_without_mutating_transcript(monkeypatch):
    class TtyTerminal(_FakeTerminal):
        is_tty = True
        columns = 50
        rows = 12

        def copy_to_clipboard(self, text):
            self.copied = text
            return True

    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    terminal = TtyTerminal()
    terminal.copied = ""
    app = TuiApplication(HarnessStub(), ApprovalBroker())
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.transcript = [TranscriptItem("assistant", "hello world")]
    before = list(app.state.transcript)
    app._handle_key("MOUSE_LEFT_DOWN:2:3")
    app._handle_key("MOUSE_LEFT_UP:7:3")
    assert terminal.copied == "hello"
    assert app.state.copy_status == "Copied"
    assert app.state.transcript == before


def test_ctrl_c_copies_active_selection_when_idle():
    class TtyTerminal(_FakeTerminal):
        is_tty = True

        def copy_to_clipboard(self, text):
            self.copied = text
            return True

    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    terminal = TtyTerminal()
    terminal.copied = ""
    app = TuiApplication(HarnessStub(), ApprovalBroker())
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.transcript = [TranscriptItem("assistant", "copy me")]
    app.state.selection_anchor = (2, 3)
    app.state.selection_focus = (8, 3)
    app._handle_key("\x03")
    assert terminal.copied == "copy me"
    assert app.state.mode == "idle"


def test_ctrl_d_clears_input_before_exiting_and_escape_aborts():
    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def abort(self):
            self.is_running = False

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.editor.handle("x")
    app._handle_key("\x04")
    assert app.state.mode == "idle"
    assert app.editor.text == ""
    app.state.mode = "working"
    app._handle_key("ESC")
    assert app.editor.text == ""


def test_permission_state_renders_visible_approval_panel():
    terminal = _FakeTerminal()
    state = UiState(mode="permission", status="waiting for approval", input_text="")
    from cli.state import ToolView

    state.active_tool = ToolView("exe", {"cmd": "echo test"})
    ScreenRenderer(terminal).render(state)
    output = terminal.output.getvalue()
    assert "permission required" in output
    assert "echo test" in output


def test_approval_broker_pending_flag_is_visible_across_thread_boundary():
    broker = ApprovalBroker()
    result = []

    import threading
    import time

    worker = threading.Thread(target=lambda: result.append(broker.ask("Allow?")))
    worker.start()
    for _ in range(100):
        if broker.pending:
            break
        time.sleep(0.001)
    assert broker.pending is True
    assert broker.submit("y") is True
    worker.join(1)
    assert result == ["y"]
    assert broker.pending is False


def test_tui_permission_request_can_be_answered_without_deadlock():
    from core.types import ToolCall
    from harness.app import Harness
    from cli.repl import create_approval_handler
    from providers.base import FakeProvider
    import time

    broker = ApprovalBroker()
    harness = Harness(
        FakeProvider([[ToolCall("c1", "exe", {"cmd": "echo test"})], "done"]),
        "workspace",
        approval_handler=create_approval_handler(broker),
    )
    app = TuiApplication(harness, broker)
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app._start_prompt("run command")
    for _ in range(200):
        app._drain_events()
        app._update_permission_state()
        if broker.pending:
            break
        time.sleep(0.005)
    assert broker.pending is True
    assert app.state.mode == "permission"
    app._handle_key("y")
    for _ in range(200):
        app._drain_events()
        if not harness.is_running:
            break
        time.sleep(0.005)
    assert harness.is_running is False
    assert app.state.active_tool is None


def test_tui_permission_mode_command_does_not_resolve_to_session_selector():
    class HarnessStub:
        is_running = False
        session_id = "session-id"
        session_name = None

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def permission_mode(self):
            return "default"

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app._run_command("/perm")
    assert any("permission_mode" in item.text for item in app.state.transcript)
    assert not any("Available sessions" in item.text for item in app.state.transcript)


def test_resume_and_drop_use_in_tui_overlays_without_blocking_input():
    from pathlib import Path

    class HarnessStub:
        is_running = False
        session_id = "current-session"
        session_name = None

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def session_catalog(self):
            return [{"id": "other-session", "name": "demo", "first_prompt": "hello", "modified": 0}]

        def resume_session(self, identifier):
            assert identifier == "other-session"
            self.session_id = identifier
            return Path("other.jsonl")

        def drop_session(self, identifier):
            assert identifier == "other-session"
            return Path("other.jsonl")

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.transcript.append(TranscriptItem("user", "/resume"))
    app._open_resume_overlay()
    assert app.state.overlay_kind == "resume"
    app._handle_overlay_key("\r")
    assert app.state.overlay_kind is None
    assert app.state.session_id == "other-session"
    assert any("[resume]" in item.text for item in app.state.transcript)

    app.state.transcript.append(TranscriptItem("user", "/drop other-session"))
    app._open_drop_overlay("/drop other-session")
    assert app.state.overlay_kind == "drop"
    app._handle_overlay_key("n")
    assert app.state.overlay_kind is None
    assert app.state.transcript[-1].text == "[drop] cancelled"


def test_resume_selector_renders_pi_style_header_and_highlight():
    class TtyTerminal(_FakeTerminal):
        is_tty = True
        columns = 100
        rows = 30

    terminal = TtyTerminal()
    state = UiState(overlay_kind="resume", overlay_items=["(unnamed) [a] hello", "(unnamed) [b] world"], overlay_index=1)
    lines, _ = ScreenRenderer(terminal)._lines(state)
    text = "\n".join(lines)
    assert "Resume Session (Current Folder)" in text
    assert "Current Folder" in text
    assert "hello" in text and "world" in text
    assert "\x1b[7m" in text


def test_resume_selector_mouse_click_changes_focused_session():
    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.overlay_kind = "resume"
    app.state.overlay_items = ["one", "two"]
    app.state.overlay_ids = ["a", "b"]
    app._handle_key("MOUSE_LEFT_DOWN:2:7")
    assert app.state.overlay_index == 1


def test_tree_selector_uses_wheel_and_enter_for_checkout():
    from runtime.session.types import SessionTreeNode

    class HarnessStub:
        is_running = False
        session_id = "session-id"
        session_name = None
        checked_out = None

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def session_tree(self):
            return [
                SessionTreeNode("root", None, "message", "user", "hello", 0, ("child",), True, False),
                SessionTreeNode("child", "root", "message", "assistant", "answer", 1, (), True, True),
            ]

        def checkout(self, message_id):
            self.checked_out = message_id

    harness = HarnessStub()
    app = TuiApplication(harness, ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app._open_tree_overlay()
    assert app.state.overlay_kind == "tree"
    assert app.state.overlay_index == 1
    app._handle_key("MOUSE_WHEEL_UP")
    assert app.state.overlay_index == 0
    app._handle_overlay_key("\r")
    assert harness.checked_out == "root"
    assert app.state.overlay_kind is None
    assert any("[checkout]" in item.text for item in app.state.transcript)
