from pathlib import Path

from core.types import Message, ToolCall, ToolResult, ToolSpec
from providers.base import FakeProvider
from runtime.execution import LocalExecutionEnv
from runtime.permissions import AllowAllPermissions
from runtime.session import JsonlSessionStore, default_session_path
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
    path = default_session_path(tmp_path)
    assert path.parent == tmp_path.resolve() / ".agent" / "sessions"
    assert path.suffix == ".jsonl"