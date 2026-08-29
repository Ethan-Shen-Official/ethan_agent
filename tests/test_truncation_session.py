from pathlib import Path

from core.types import Message, ToolCall, ToolResult, ToolSpec
from providers.base import FakeProvider
from runtime.execution import LocalExecutionEnv
from runtime.permissions import AllowAllPermissions
from core.errors import SessionError
from runtime.session import JsonlSessionStore, default_session_path, latest_session_path
from tools.base import ToolBase, ToolContext
from tools.executor import ToolExecutor, ToolOutputLimits
from tools.registry import ToolRegistry
from tools.truncate import truncate_head, truncate_tail


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(LocalExecutionEnv(tmp_path), AllowAllPermissions())


def test_head_truncation_keeps_complete_lines_and_metadata():
    result = truncate_head("one\ntwo\nthree", max_lines=2, max_bytes=100)
    assert result.content == "one\ntwo"
    assert result.truncated is True
    assert result.truncated_by == "lines"
    assert result.total_lines == 3
    assert result.output_lines == 2


def test_tail_truncation_keeps_last_lines():
    result = truncate_tail("one\ntwo\nthree", max_lines=2, max_bytes=100)
    assert result.content == "two\nthree"
    assert result.truncated is True
    assert result.truncated_by == "lines"


def test_tail_truncation_respects_utf8_bytes():
    result = truncate_tail("prefix-\U0001f642-end", max_lines=10, max_bytes=4)
    assert len(result.content.encode("utf-8")) <= 4
    assert result.content.endswith("-end")
    assert result.partial_line is True


class _LongOutputTool(ToolBase):
    def __init__(self, name: str, content: str) -> None:
        self.spec = ToolSpec(name, "long output", {"type": "object", "properties": {}})
        self.content = content

    def run(self, arguments, context):
        return self.content


def test_executor_uses_tail_for_exe_and_head_for_other_tools(tmp_path: Path):
    output = "\n".join(f"line-{i}" for i in range(5))
    registry = ToolRegistry(
        [_LongOutputTool("read_file", output), _LongOutputTool("exe", output)]
    )
    executor = ToolExecutor(
        registry,
        _context(tmp_path),
        output_limits=ToolOutputLimits(max_lines=2, max_bytes=1000),
    )
    results = {
        event.data["result"].name: event.data["result"]
        for event in executor.execute(
            [
                ToolCall("head", "read_file", {}),
                ToolCall("tail", "exe", {}),
            ]
        )
        if event.kind == "tool_result"
    }
    assert results["read_file"].content.startswith("line-0\nline-1")
    assert results["exe"].content.startswith("line-3\nline-4")
    assert results["read_file"].truncated is True
    assert results["exe"].truncated is True
    assert "[Output truncated:" in results["exe"].content


def test_jsonl_session_store_round_trips_messages_and_tool_metadata(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    store = JsonlSessionStore(path, session_id="session-1", operation_id="op-1")
    result = ToolResult(
        "call-1",
        "exe",
        "tail",
        True,
        True,
        "bytes",
        10,
        100,
        2,
        20,
    )
    messages = [
        Message.user("run"),
        Message.assistant("", [ToolCall("call-1", "exe", {"cmd": "echo ok"})]),
        Message.tool(result),
    ]
    for message in messages:
        store.append(message)

    restored_store = JsonlSessionStore(path)
    restored = restored_store.read()
    assert restored_store.session_id == "session-1"
    assert restored_store.operation_id != "op-1"
    assert restored == messages
    assert restored[-1].tool_result is not None
    assert restored[-1].tool_result.truncated_by == "bytes"


def test_harness_restores_history_between_instances(tmp_path: Path):
    from harness.app import Harness

    path = tmp_path / "history.jsonl"
    first = Harness(FakeProvider(["done"]), str(tmp_path), session_path=path)
    list(first.prompt("first prompt"))
    second = Harness(FakeProvider(["done"]), str(tmp_path), session_path=path)
    assert [message.role for message in second.state.messages] == ["user", "assistant"]
    assert second.state.messages[0].content == "first prompt"

def test_default_session_path_is_workspace_local(tmp_path: Path):
    import re

    first = default_session_path(tmp_path)
    second = default_session_path(tmp_path)
    assert first.parent == tmp_path.resolve() / ".agent" / "sessions"
    assert first.suffix == ".jsonl"
    assert first != second
    assert re.fullmatch(r"\d{8}-\d{6}-\d{3}_[0-9a-f]{12}", first.stem)


def test_latest_session_path_selects_most_recent_file(tmp_path: Path):
    directory = tmp_path / ".agent" / "sessions"
    directory.mkdir(parents=True)
    older = directory / "20260829-010000_old.jsonl"
    newer = directory / "20260829-020000_new.jsonl"
    older.write_text("", encoding="utf-8")
    newer.write_text("", encoding="utf-8")
    import os

    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    assert latest_session_path(tmp_path) == newer


def test_harness_starts_new_session_by_default_and_can_resume_latest(tmp_path: Path):
    from harness.app import Harness

    first = Harness(FakeProvider(["one"]), str(tmp_path))
    list(first.prompt("first"))
    first_path = first.session_store.path
    second = Harness(FakeProvider(["two"]), str(tmp_path))
    assert second.session_store.path != first_path
    assert second.state.messages == []
    list(second.prompt("second"))
    resumed = Harness(FakeProvider(["resumed"]), str(tmp_path), resume=True)
    assert resumed.session_store.path == second.session_store.path
    assert [message.content for message in resumed.state.messages] == ["second", "two"]


def test_session_store_replays_active_branch_and_preserves_old_branch(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    store = JsonlSessionStore(path, session_id="tree")
    root = Message.user("root")
    left = Message.assistant("left")
    right = Message.assistant("right")
    store.append(root)
    root_id = store.current_leaf_id
    store.append(left)
    left_id = store.current_leaf_id
    store.checkout(root_id)
    store.append(right)
    right_id = store.current_leaf_id

    restored = JsonlSessionStore(path)
    assert [message.content for message in restored.read()] == ["root", "right"]
    assert len(restored.read_all()) == 3
    assert {record.message.content for record in restored.children(root_id)} == {"left", "right"}
    assert restored.get_record(left_id).message.content == "left"
    assert restored.current_leaf_id == right_id


def test_session_store_does_not_checkout_inside_tool_turn(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    store = JsonlSessionStore(path)
    call = ToolCall("call", "write", {"path": "x.txt", "content": "x"})
    store.append(Message.user("do it"))
    store.append(Message.assistant("", [call]))
    assistant_id = store.current_leaf_id
    with pytest.raises(SessionError, match="incomplete tool turn"):
        store.checkout(assistant_id)


def test_harness_checkout_reloads_active_branch(tmp_path: Path):
    from harness.app import Harness

    path = tmp_path / "tree.jsonl"
    harness = Harness(FakeProvider(["first", "second"]), str(tmp_path), session_path=path)
    list(harness.prompt("first prompt"))
    root_id = JsonlSessionStore(path).current_path()[0].message_id
    list(harness.prompt("second prompt"))
    harness.checkout(root_id)
    assert [message.content for message in harness.state.messages] == ["first prompt"]


def test_harness_rollback_without_id_returns_to_previous_user_turn(tmp_path: Path):
    from harness.app import Harness

    path = tmp_path / "rollback.jsonl"
    harness = Harness(FakeProvider(["one", "two"]), str(tmp_path), session_path=path)
    list(harness.prompt("first"))
    list(harness.prompt("second"))
    harness.rollback()
    assert [message.content for message in harness.state.messages] == ["first", "one"]


def test_repl_checkout_command_switches_harness_branch(tmp_path: Path, capsys):
    from cli.main import handle_repl_command
    from harness.app import Harness

    path = tmp_path / "commands.jsonl"
    harness = Harness(FakeProvider(["one", "two"]), str(tmp_path), session_path=path)
    list(harness.prompt("first"))
    first_id = JsonlSessionStore(path).current_path()[0].message_id
    list(harness.prompt("second"))
    assert handle_repl_command(f"/checkout {first_id[:8]}", harness) is True
    assert [message.content for message in harness.state.messages] == ["first"]
    assert "active message" in capsys.readouterr().out
