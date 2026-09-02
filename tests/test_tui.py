from io import StringIO

from cli.ui.input import InputEditor
from cli.ui.reducer import reduce_event
from cli.ui.renderer import ScreenRenderer
from cli.ui.state import TranscriptItem, ToolView, UiState
from cli.ui.terminal import TerminalBackend
from cli.ui.markdown import render_markdown
from cli.ui.tool_render import edit_diff_lines
from cli.ui.app import TuiApplication
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


def test_styled_markdown_marks_headings_code_paths_and_lists():
    from cli.ui.markdown import render_markdown_styled

    text = "# Title\nUse `src/app.py` and **bold**\n- item"
    lines = render_markdown_styled(text, 60)
    assert any("\x1b[33;1m" in line for line in lines)
    assert any("\x1b[36m" in line and "src/app.py" in line for line in lines)
    assert any("\x1b[38;5;116m" in line for line in lines)


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
    reduce_event(state, AgentEvent("tool_start", {"id": "c1", "name": "find", "arguments": {}}))
    assert state.mode == "working"
    assert state.active_tool is not None
    reduce_event(state, AgentEvent("tool_result", {"result": object()}))
    assert state.active_tool is None
    assert state.transcript == []
    from core.types import ToolResult

    reduce_event(state, AgentEvent("tool_result", {"result": ToolResult("c1", "find", "result text")}))
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
    assert state.last_input_tokens == 6300


def test_context_meter_refreshes_after_new_messages_are_added():
    from types import SimpleNamespace

    class HarnessStub:
        is_running = False
        session_id = "session-id"
        execution_env = SimpleNamespace(cwd="D:/workspace")
        compact_config = SimpleNamespace(context_window=100)

        def __init__(self):
            self.projected = 0
            self.compaction = SimpleNamespace(projected_token_count=lambda: self.projected)

    harness = HarnessStub()
    app = TuiApplication(harness, ApprovalBroker())
    assert app.state.context_percent == 0.0
    harness.projected = 35
    app._draw()
    assert app.state.context_percent == 35.0

    reduce_event(app.state, AgentEvent("usage", {"input_tokens": 72, "tokens": 80}))
    app._draw()
    assert app.state.context_percent == 72.0


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


def test_empty_tui_uses_stable_ascii_header_and_context():
    terminal = _FakeTerminal()
    terminal.rows = 30
    state = UiState(cwd="D:/workspace", startup_context=("AGENTS.md",))
    lines, _ = ScreenRenderer(terminal)._lines(state)
    output = "\n".join(lines)
    assert "| | | |" in output
    assert "coding-agent" in output
    assert "Tip: Type /help" in output
    assert "[Context]" in output
    assert "AGENTS.md" in output


def test_startup_greeting_remains_visible_while_typing():
    terminal = _FakeTerminal()
    terminal.rows = 30
    state = UiState(cwd="D:/workspace", input_text="hello", cursor_position=5)
    lines, _ = ScreenRenderer(terminal)._lines(state)
    output = "\n".join(lines)
    assert "| | | |" in output
    assert "agent> hello" in output


def test_header_labels_hello_user_and_does_not_show_permission_mode():
    terminal = _FakeTerminal()
    terminal.rows = 30
    state = UiState(cwd="D:/workspace", permission_mode="bypass_permissions")
    output = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "Hello!User" in output
    assert "permission" not in output.lower()


def test_resumed_context_meter_is_seeded_from_compaction_projection():
    from types import SimpleNamespace

    class HarnessStub:
        is_running = False
        session_id = "session-id"
        execution_env = SimpleNamespace(cwd="D:/workspace")
        compact_config = SimpleNamespace(context_window=200)
        compaction = SimpleNamespace(projected_token_count=lambda: 40)

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    assert app.state.context_window == 200
    assert app.state.context_percent == 20.0


def test_slash_command_output_has_unstyled_gap_after_user_block():
    terminal = _FakeTerminal()
    terminal.rows = 40
    state = UiState(cwd="D:/workspace")
    state.transcript = [
        TranscriptItem("user", "/help"),
        TranscriptItem("system", "Available commands: /help"),
    ]
    lines, _ = ScreenRenderer(terminal)._lines(state)
    user_end = next(i for i, line in enumerate(lines) if line.strip().startswith("› /help"))
    # User blocks end with a colored padding row, then a plain separator row
    # before the command output starts.
    assert "Available commands" in "\n".join(lines[user_end + 1 :])
    command_index = next(i for i, line in enumerate(lines) if "Available commands" in line)
    assert lines[command_index - 1] == ""


def test_slash_command_output_uses_normal_terminal_background():
    class TtyTerminal(_FakeTerminal):
        is_tty = True

    terminal = TtyTerminal()
    terminal.rows = 40
    state = UiState(cwd="D:/workspace")
    state.transcript = [TranscriptItem("system", "Available commands: /help")]
    output = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "Available commands" in output
    assert "48;2;55;55;65" not in output


def test_startup_context_does_not_inherit_parent_directory():
    # The workspace used by this test has no context file of its own while
    # the repository root does; discovery must remain workspace-local.
    context = TuiApplication._discover_startup_context("D:/NJU/codeagent/workspace")
    assert context == ("No System Context File",)


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
                Message.assistant(tool_calls=[ToolCall("c1", "read", {"path": "large.txt"})]),
                Message.tool(ToolResult("c1", "read", "\n".join(f"line {i}" for i in range(30)))),
            ]
        )

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    tool = app.state.transcript[-1]
    assert tool.kind == "tool"
    assert tool.collapsed is True
    assert tool.tool_name == "read"
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
    from cli.ui.state import TranscriptItem, ToolView

    state.transcript = [TranscriptItem("user", "hello"), TranscriptItem("system", "command output")]
    state.active_tool = ToolView("read", {"path": "a.txt"})
    ScreenRenderer(terminal).render(state)
    output = terminal.output.getvalue()
    assert "48;2;52;53;65" in output  # submitted user background
    assert "48;2;55;55;65" not in output  # slash output uses normal background
    assert "running read" in output
    assert "\x1b[36;1mrunning read" in output
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
    from cli.ui.state import TranscriptItem, ToolView

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
            tool_name="read",
            tool_arguments={"path": "large.txt"},
        )
    ]
    renderer = ScreenRenderer(terminal)
    collapsed, _ = renderer._lines(state)
    collapsed_text = "\n".join(collapsed)
    assert "line 29" not in collapsed_text
    assert "lines hidden" in collapsed_text
    assert "read large.txt" in collapsed_text
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


def test_edit_diff_preview_renders_canonical_edit_list():
    lines, omitted = edit_diff_lines(
        {"edits": [{"oldText": "old line", "newText": "new line"}]}
    )
    assert omitted == 0
    assert any(line.startswith("- ") and "old line" in line for line in lines)
    assert any(line.startswith("+ ") and "new line" in line for line in lines)


def test_ls_running_summary_does_not_dump_partial_json():
    terminal = _FakeTerminal()
    state = UiState(cwd="D:/workspace", active_tool=ToolView("ls", {"path": ".", "depth": 2}))
    text = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "running ls . (depth=2)" in text
    assert '{"path"' not in text


def test_completed_read_result_keeps_compact_file_header():
    terminal = _FakeTerminal()
    state = UiState(cwd="D:/workspace")
    state.transcript = [
        TranscriptItem(
            "tool",
            "\n".join(f"line {index}" for index in range(30)),
            collapsed=True,
            tool_name="read",
            tool_arguments={"path": "src/main.py"},
        )
    ]
    text = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "read src/main.py" in text
    assert "line 29" not in text


def test_renderer_scroll_offset_moves_only_conversation_viewport():
    terminal = _FakeTerminal()
    terminal.rows = 8
    state = UiState(cwd="D:/workspace")
    from cli.ui.state import TranscriptItem

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
    from cli.ui.state import ToolView

    state.active_tool = ToolView("bash", {"command": "echo test"})
    ScreenRenderer(terminal).render(state)
    output = terminal.output.getvalue()
    assert "permission required" in output
    assert "echo test" in output


def test_confirmation_editor_uses_yellow_confirm_prompt():
    terminal = _FakeTerminal()
    terminal.is_tty = True
    state = UiState(mode="permission", input_text="y")
    lines, _ = ScreenRenderer(terminal)._lines(state)
    editor_line = next(line for line in lines if "confirm(y/n)>" in line)
    assert "\x1b[33;1m" in editor_line


def test_waiting_approval_status_uses_confirmation_prompt_before_mode_sync():
    terminal = _FakeTerminal()
    terminal.is_tty = True
    state = UiState(mode="working", status="waiting for approval", input_text="")
    lines, _ = ScreenRenderer(terminal)._lines(state)
    editor_line = next(line for line in lines if "confirm(y/n)>" in line)
    assert "\x1b[33;1m" in editor_line


def test_user_and_tool_blocks_share_horizontal_padding():
    terminal = _FakeTerminal()
    state = UiState(
        transcript=[
            TranscriptItem("user", "question"),
            TranscriptItem("tool", "result", tool_name="bash", tool_arguments={"command": "echo ok"}),
        ]
    )
    lines, _ = ScreenRenderer(terminal)._lines(state)
    user_line = next(line for line in lines if "question" in line)
    tool_line = next(line for line in lines if "echo ok" in line)
    assert " question" in user_line
    assert " echo ok" in tool_line


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
        FakeProvider([[ToolCall("c1", "bash", {"command": "echo test"})], "done"]),
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


def test_compact_command_runs_in_background_and_exposes_status():
    import threading
    import time
    from types import SimpleNamespace

    class Provider:
        def abort(self):
            return None

    class HarnessStub:
        is_running = False
        session_id = "session-id"
        provider = Provider()

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def compact(self):
            self.started.set()
            self.release.wait(1)
            return SimpleNamespace(summarized_count=2, kept_count=1)

    harness = HarnessStub()
    app = TuiApplication(harness, ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.transcript.append(TranscriptItem("user", "/compact"))
    app._start_background_command("/compact")
    assert harness.started.wait(1)
    assert app._command_running is True
    assert app.state.status == "compacting"
    text = "\n".join(ScreenRenderer(terminal)._lines(app.state)[0])
    assert "compacting context" in text
    harness.release.set()
    for _ in range(100):
        app._drain_events()
        if not app._command_running:
            break
        time.sleep(0.005)
    assert app._command_running is False
    assert app.state.status == "completed"
    assert any("summarized 2 messages" in item.text for item in app.state.transcript)


def test_abort_cancels_background_compact_and_keeps_input_live():
    import threading
    import time

    class Provider:
        def __init__(self, release):
            self.release = release
            self.aborted = False

        def abort(self):
            self.aborted = True
            self.release.set()

    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.provider = Provider(self.release)

        def compact(self):
            self.started.set()
            self.release.wait(1)
            return None

    harness = HarnessStub()
    app = TuiApplication(harness, ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.transcript.append(TranscriptItem("user", "/compact"))
    app._start_background_command("/compact")
    assert harness.started.wait(1)
    for char in "/abort":
        app._handle_key(char)
    app._handle_key("\r")
    assert harness.provider.aborted is True
    assert app.state.status == "cancelling"
    for _ in range(100):
        app._drain_events()
        if not app._command_running:
            break
        time.sleep(0.005)
    assert app._command_running is False
    assert app.state.status == "cancelled"


def test_prompt_is_rejected_while_compact_is_running():
    import threading

    class Provider:
        def abort(self):
            return None

    class HarnessStub:
        is_running = False
        session_id = "session-id"
        provider = Provider()

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def __init__(self):
            self.release = threading.Event()

        def compact(self):
            self.release.wait(1)

    harness = HarnessStub()
    app = TuiApplication(harness, ApprovalBroker())
    app._start_background_command("/compact")
    for char in "hello":
        app._handle_key(char)
    app._handle_key("\r")
    assert app.worker is not None
    assert app.state.transcript[-1].kind == "system"
    assert "use /abort" in app.state.transcript[-1].text
    harness.release.set()
    app.worker.join(1)


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
            assert identifier == "stale-session"
            return Path("stale.jsonl")

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

    app.state.transcript.append(TranscriptItem("user", "/drop stale-session"))
    app._open_drop_overlay("/drop stale-session")
    assert app.state.overlay_kind == "drop"
    app._handle_overlay_key("n")
    assert app.state.overlay_kind is None
    assert app.state.transcript[-1].text == "[drop] cancelled"


def test_drop_selector_excludes_active_session_and_deletes_selected_entry():
    from pathlib import Path

    class HarnessStub:
        is_running = False
        session_id = "runtime-current-session"
        session_name = None
        session_path = Path("D:/workspace/.agent/sessions/current.jsonl")

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def __init__(self):
            self.deleted = []

        def session_catalog(self):
            return [
                {
                    "id": "empty-current-file-stem",
                    "path": self.session_path,
                    "name": "current",
                    "first_prompt": "",
                    "modified": 0,
                },
                {
                    "id": "old-session-a",
                    "path": Path("D:/workspace/.agent/sessions/a.jsonl"),
                    "name": "first",
                    "first_prompt": "hello",
                    "modified": 1,
                },
                {
                    "id": "old-session-b",
                    "path": Path("D:/workspace/.agent/sessions/b.jsonl"),
                    "name": "second",
                    "first_prompt": "world",
                    "modified": 2,
                },
            ]

        def drop_session(self, identifier):
            assert identifier != self.session_id
            self.deleted.append(identifier)
            return Path(f"{identifier}.jsonl")

    harness = HarnessStub()
    app = TuiApplication(harness, ApprovalBroker())
    terminal = _FakeTerminal()
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.transcript.append(TranscriptItem("user", "/drop"))
    app._open_drop_overlay("/drop")

    assert app.state.overlay_kind == "drop"
    assert app.state.overlay_value == ""
    assert app.state.overlay_ids == ["old-session-a", "old-session-b"]
    app._handle_key("DOWN")
    app._handle_key("\r")
    assert app.state.overlay_value == "old-session-b"
    assert app.state.status == "waiting for confirmation"
    app._handle_key("y")
    assert harness.deleted == ["old-session-b"]
    assert app.state.overlay_kind is None
    assert "[drop] deleted:" in app.state.transcript[-1].text


def test_drop_rejects_active_session_before_confirmation():
    class HarnessStub:
        is_running = False
        session_id = "active-session-123456"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

        def drop_session(self, identifier):
            raise AssertionError("active session must not reach deletion")

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    app._open_drop_overlay("/drop active-session")
    assert app.state.overlay_kind is None
    assert "Cannot drop the active session" in app.state.transcript[-1].text


def test_drop_selector_renders_highlight_and_select_prompt():
    terminal = _FakeTerminal()
    terminal.rows = 30
    state = UiState(
        overlay_kind="drop",
        overlay_items=["first [a] hello", "second [b] world"],
        overlay_ids=["a", "b"],
        overlay_index=1,
    )
    lines, _ = ScreenRenderer(terminal)._lines(state)
    text = "\n".join(lines)
    assert "Delete Session (Current Folder)" in text
    assert "Current session is protected and hidden" in text
    assert "first" in text and "second" in text
    assert "select>" in text


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


def test_tree_selector_pages_long_history_and_keeps_selected_row_visible():
    terminal = _FakeTerminal()
    terminal.rows = 14
    state = UiState(
        overlay_kind="tree",
        overlay_items=[f"• user: node {index}" for index in range(30)],
        overlay_ids=[f"id-{index}" for index in range(30)],
        overlay_index=29,
    )
    renderer = ScreenRenderer(terminal)
    lines, _ = renderer._lines(state)
    text = "\n".join(lines)

    assert state.overlay_scroll > 0
    assert "node 29" in text
    assert "node 0" not in text
    assert len(lines) <= terminal.rows


def test_tree_selector_moves_window_with_arrow_and_page_keys():
    class HarnessStub:
        is_running = False
        session_id = "session-id"

        class Env:
            cwd = "D:/workspace"

        execution_env = Env()

    app = TuiApplication(HarnessStub(), ApprovalBroker())
    terminal = _FakeTerminal()
    terminal.rows = 14
    app.terminal = terminal
    app.renderer = ScreenRenderer(terminal)
    app.state.overlay_kind = "tree"
    app.state.overlay_items = [f"• user: node {index}" for index in range(30)]
    app.state.overlay_ids = [f"id-{index}" for index in range(30)]
    app.state.overlay_index = 0

    for _ in range(10):
        app._handle_overlay_key("DOWN")
    assert app.state.overlay_index == 10
    assert app.state.overlay_scroll > 0
    before = app.state.overlay_index
    app._handle_overlay_key("RIGHT")
    assert app.state.overlay_index > before
    app._handle_overlay_key("LEFT")
    assert app.state.overlay_index == before


def test_working_state_renders_pi_style_spinner_and_changes_frame():
    terminal = _FakeTerminal()
    renderer = ScreenRenderer(terminal)
    state = UiState(mode="working", status="working")
    first = "\n".join(renderer._lines(state)[0])
    state.spinner_frame += 1
    second = "\n".join(renderer._lines(state)[0])
    assert "Working..." in first
    assert "Working..." in second
    assert first != second


def test_tree_selector_styles_user_assistant_and_tool_roles():
    class TtyTerminal(_FakeTerminal):
        is_tty = True

    terminal = TtyTerminal()
    state = UiState(
        overlay_kind="tree",
        overlay_items=["• user: hello", "• assistant: answer", "• [bash: dir]"],
        overlay_roles=["user", "assistant", "tool"],
        overlay_ids=["u", "a", "t"],
        overlay_index=0,
        terminal_height=20,
    )
    text = "\n".join(ScreenRenderer(terminal)._lines(state)[0])
    assert "\x1b[36;1m" in text
    assert "\x1b[97;1m" in text
    assert "\x1b[33;1m" in text
    # Focused row remains reverse-video rather than being overwritten by the
    # role color.
    assert "\x1b[7m" in text


def test_tree_overlay_uses_compact_branch_connectors_for_forks():
    from types import SimpleNamespace
    from cli.views.tree import build_tree_overlay

    node = SimpleNamespace
    nodes = [
        node(message_id="root", parent_id=None, depth=0, role="assistant", preview="root", children_ids=("left", "right"), is_active=True, is_leaf=False),
        node(message_id="left", parent_id="root", depth=1, role="user", preview="left", children_ids=("left-tool",), is_active=False, is_leaf=False),
        node(message_id="left-tool", parent_id="left", depth=2, role="tool", preview="bash: left", children_ids=(), is_active=False, is_leaf=False),
        node(message_id="right", parent_id="root", depth=1, role="user", preview="right", children_ids=(), is_active=False, is_leaf=True),
    ]
    view = build_tree_overlay(nodes)
    assert "├⊟ • user: left" in view.items[1]
    assert view.items[2].startswith("│  • [bash: left]")
    assert view.items[3].startswith("└⊟ * user: right")
    assert view.roles[2] == "tool"


def test_tree_overlay_keeps_linear_chain_on_one_column_until_fork():
    from types import SimpleNamespace
    from cli.views.tree import build_tree_overlay

    node = SimpleNamespace
    nodes = [
        node(message_id="r", parent_id=None, depth=0, role="user", preview="root", children_ids=("a",), is_active=True, is_leaf=False),
        node(message_id="a", parent_id="r", depth=1, role="assistant", preview="answer", children_ids=("t",), is_active=True, is_leaf=False),
        node(message_id="t", parent_id="a", depth=2, role="tool", preview="bash: ls", children_ids=(), is_active=True, is_leaf=True),
    ]
    view = build_tree_overlay(nodes)
    assert view.items == ["• user: root", "• assistant: answer", "* [bash: ls]"]
