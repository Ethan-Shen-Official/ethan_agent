from pathlib import Path

from core.context import DefaultContextBuilder
from core.loop import AgentLoop, LoopConfig
from core.state import LoopState
from core.types import ToolCall
from harness.app import Harness
from providers.base import FakeProvider
from runtime.execution import LocalExecutionEnv
from runtime.permissions import AllowAllPermissions
from tools.base import ToolContext
from tools.executor import ToolExecutor
from tools.filesystem import ReadFileTool, SearchTool, WriteFileTool
from tools.registry import ToolRegistry
from tools.shell import ShellTool


def make_loop(provider, cwd=".", max_turns=8):
    registry = ToolRegistry([ReadFileTool(), WriteFileTool(), SearchTool(), ShellTool()])
    context = ToolContext(LocalExecutionEnv(cwd), AllowAllPermissions())
    return AgentLoop(provider, ToolExecutor(registry, context), registry.specs(), LoopConfig(max_turns=max_turns))


def test_text_response_completes():
    events = list(make_loop(FakeProvider(["hello"])).run("say hello"))
    assert [event.data["text"] for event in events if event.kind == "text_delta"] == ["hello"]
    assert events[-1].kind == "turn_end"
    assert events[-1].data["reason"] == "completed"
    assert events[-1].data["message"] == "hello"


def test_text_deltas_stream_in_provider_order():
    class ChunkedProvider:
        def stream(self, request):
            from core.types import ProviderEvent

            yield ProviderEvent("text_delta", text="first ")
            yield ProviderEvent("text_delta", text="second")
            yield ProviderEvent("done")

    events = list(make_loop(ChunkedProvider()).run("stream"))
    assert [event.data["text"] for event in events if event.kind == "text_delta"] == ["first ", "second"]
    assert events[-1].data["message"] == "first second"


def test_tool_call_round_trip(tmp_path: Path):
    call = ToolCall("c1", "write", {"path": "note.txt", "content": "done"})
    provider = FakeProvider([[call], "finished"])
    events = list(make_loop(provider, tmp_path).run("create a note"))
    assert (tmp_path / "note.txt").read_text() == "done"
    assert [e.kind for e in events].count("tool_result") == 1
    assert events[-1].data["reason"] == "completed"


def test_unknown_tool_is_returned_as_error():
    call = ToolCall("c1", "missing", {})
    events = list(make_loop(FakeProvider([[call], "recovered"])).run("use tool"))
    result = next(e.data["result"] for e in events if e.kind == "tool_result")
    assert result.is_error is True
    assert "unknown tool" in result.content


def test_invalid_tool_arguments_are_returned_as_error(tmp_path: Path):
    call = ToolCall("c1", "write", {"path": "note.txt"})
    events = list(make_loop(FakeProvider([[call], "recovered"]), tmp_path).run("use tool"))
    result = next(e.data["result"] for e in events if e.kind == "tool_result")
    assert result.is_error is True
    assert "missing required argument" in result.content


def test_max_turns_stops_tool_loop():
    call = ToolCall("c1", "search", {"pattern": "*.py"})
    events = list(make_loop(FakeProvider([[call], [call], [call]]), max_turns=2).run("loop"))
    assert events[-1].data["reason"] == "max_turns"


def test_cancel_before_run():
    loop = make_loop(FakeProvider(["never"]))
    state = LoopState()
    state.request_cancel()
    events = list(loop.run("cancel", state))
    assert events[-1].data["reason"] == "cancelled"


def test_builtin_tools(tmp_path: Path):
    env = LocalExecutionEnv(tmp_path)
    context = ToolContext(env, AllowAllPermissions())
    assert WriteFileTool().execute({"path": "a.txt", "content": "abc"}, context).is_error is False
    assert ReadFileTool().execute({"path": "a.txt"}, context).content == "abc"
    assert SearchTool().execute({"pattern": "*.txt"}, context).content == "a.txt"
    result = ShellTool().execute({"cmd": "echo ok"}, context)
    assert result.is_error is False
    assert "ok" in result.content


def test_harness_wires_default_tools(tmp_path: Path):
    harness = Harness(FakeProvider(["ready"]), str(tmp_path))
    events = list(harness.prompt("status"))
    assert events[-1].data["reason"] == "completed"
    assert harness.execution_env.cwd == tmp_path.resolve()


def test_context_includes_workspace_root(tmp_path: Path):
    builder = DefaultContextBuilder(str(tmp_path))
    request = builder.build(LoopState(), (), "base prompt")
    assert str(tmp_path.resolve()) in request.system_prompt
    assert "Keep all file changes inside this workspace" in request.system_prompt


def test_workspace_rejects_escape(tmp_path: Path):
    env = LocalExecutionEnv(tmp_path)
    try:
        env.write_file("../outside.txt", "blocked")
    except PermissionError as exc:
        assert "outside workspace" in str(exc)
    else:
        raise AssertionError("workspace escape was not rejected")


def test_harness_resets_turn_budget_per_prompt(tmp_path: Path):
    call = ToolCall("c1", "write", {"path": "note.txt", "content": "done"})
    harness = Harness(FakeProvider([[call], "first done", "second done"]), str(tmp_path), max_turns=2)
    first = list(harness.prompt("create a note"))
    second = list(harness.prompt("say something else"))
    assert first[-1].data["reason"] == "completed"
    assert second[-1].data["reason"] == "completed"


def test_context_discovers_runtime_metadata_and_root_instructions(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Use the project conventions.", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Do not include this instruction.", encoding="utf-8")
    context = DefaultContextBuilder(str(tmp_path), model_name="test-model").workspace_context
    assert context is not None
    assert context.model == "test-model"
    assert context.entrypoint == "cli"
    assert context.current_date
    assert context.is_git_repository is False
    request = DefaultContextBuilder(str(tmp_path), model_name="test-model").build(LoopState(), (), "base")
    assert "available_tools: none" in request.system_prompt
    assert "Use the project conventions." in request.system_prompt
    assert "Do not include this instruction." not in request.system_prompt
