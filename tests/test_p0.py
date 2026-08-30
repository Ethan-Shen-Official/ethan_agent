import pytest
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
from tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, SearchTool, WriteFileTool
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


def test_begin_run_resets_cancel_state():
    loop = make_loop(FakeProvider(["ready"]))
    state = LoopState()
    state.request_cancel()
    events = list(loop.run("cancel", state))
    assert events[-1].data["reason"] == "completed"
    assert state.cancelled is False
    assert state.cancel_event.is_set() is False


def test_builtin_tools(tmp_path: Path):
    env = LocalExecutionEnv(tmp_path)
    context = ToolContext(env, AllowAllPermissions())
    assert WriteFileTool().execute({"path": "a.txt", "content": "abc"}, context).is_error is False
    assert ReadFileTool().execute({"path": "a.txt"}, context).content == "abc"
    assert SearchTool().execute({"pattern": "*.txt"}, context).content == "a.txt"
    result = ShellTool().execute({"cmd": "echo ok"}, context)
    assert result.is_error is False
    assert "ok" in result.content


def test_edit_tool_requires_unique_match_and_updates_file(tmp_path: Path):
    env = LocalExecutionEnv(tmp_path)
    context = ToolContext(env, AllowAllPermissions())
    env.write_file("a.txt", "before\\nbefore\\n")
    ambiguous = EditFileTool().execute(
        {"path": "a.txt", "old_text": "before", "new_text": "after"}, context
    )
    assert ambiguous.is_error is True
    replaced = EditFileTool().execute(
        {
            "path": "a.txt",
            "old_text": "before",
            "new_text": "after",
            "replace_all": True,
        },
        context,
    )
    assert replaced.is_error is False
    assert env.read_file("a.txt") == "after\\nafter\\n"


def test_list_dir_tool_lists_nested_entries_and_hides_dotfiles(tmp_path: Path):
    env = LocalExecutionEnv(tmp_path)
    context = ToolContext(env, AllowAllPermissions())
    env.write_file("src/main.py", "print('ok')")
    env.write_file(".secret", "hidden")
    result = ListDirTool().execute({"depth": 2}, context)
    assert result.is_error is False
    assert "src" + __import__("os").sep in result.content
    assert "src" + __import__("os").sep + "main.py" in result.content
    assert ".secret" not in result.content


def test_executor_rejects_wrong_argument_types(tmp_path: Path):
    registry = ToolRegistry([ListDirTool()])
    context = ToolContext(LocalExecutionEnv(tmp_path), AllowAllPermissions())
    executor = ToolExecutor(registry, context)
    call = ToolCall("c1", "list_dir", {"depth": "two"})
    events = list(executor.execute([call]))
    result = next(event.data["result"] for event in events if event.kind == "tool_result")
    assert result.is_error is True
    assert "must be an integer" in result.content


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


def test_before_tool_hook_can_block_without_running_tool(tmp_path: Path):
    from harness.hooks import ToolHookDecision, ToolLoopHooks

    registry = ToolRegistry([WriteFileTool()])
    context = ToolContext(LocalExecutionEnv(tmp_path), AllowAllPermissions())
    hooks = ToolLoopHooks(
        before_tool=lambda ctx: ToolHookDecision(action="block", reason="policy blocked")
    )
    executor = ToolExecutor(registry, context, hooks)
    call = ToolCall("c1", "write", {"path": "blocked.txt", "content": "no"})
    result = next(event.data["result"] for event in executor.execute([call]) if event.kind == "tool_result")
    assert result.is_error is True
    assert "policy blocked" in result.content
    assert not (tmp_path / "blocked.txt").exists()


def test_before_tool_replacement_is_validated_and_used(tmp_path: Path):
    from harness.hooks import ToolHookDecision, ToolLoopHooks

    registry = ToolRegistry([WriteFileTool()])
    context = ToolContext(LocalExecutionEnv(tmp_path), AllowAllPermissions())
    hooks = ToolLoopHooks(
        before_tool=lambda ctx: ToolHookDecision(
            action="replace_arguments",
            arguments={"path": "replacement.txt", "content": "updated"},
        )
    )
    executor = ToolExecutor(registry, context, hooks)
    call = ToolCall("c1", "write", {"path": "original.txt", "content": "original"})
    result = next(event.data["result"] for event in executor.execute([call]) if event.kind == "tool_result")
    assert result.is_error is False
    assert (tmp_path / "replacement.txt").read_text() == "updated"
    assert not (tmp_path / "original.txt").exists()


def test_after_tool_can_transform_result_and_stop_loop(tmp_path: Path):
    from harness.hooks import ToolHookDecision, ToolLoopHooks

    call = ToolCall("c1", "write", {"path": "note.txt", "content": "saved"})
    hooks = ToolLoopHooks(
        after_tool=lambda ctx: ToolHookDecision(
            action="replace_result",
            result=type(ctx.result)(ctx.result.tool_call_id, ctx.result.name, "redacted", False),
            terminate=True,
        )
    )
    harness = Harness(
        FakeProvider([[call], "must not run"]),
        str(tmp_path),
        hooks=hooks,
        permission_mode="accept_edits",
    )
    events = list(harness.prompt("write a note"))
    result = next(event.data["result"] for event in events if event.kind == "tool_result")
    assert result.content == "redacted"
    assert events[-1].data["reason"] == "hook_stop"
    assert harness.tool_executor is not None
    assert harness.execution_env.read_file("note.txt") == "saved"


def test_hook_result_cannot_change_tool_call_identity(tmp_path: Path):
    from harness.hooks import ToolHookDecision, ToolLoopHooks

    registry = ToolRegistry([WriteFileTool()])
    context = ToolContext(LocalExecutionEnv(tmp_path), AllowAllPermissions())
    def rewrite(ctx):
        return ToolHookDecision(action="replace_result", result=type(ctx.result)("other", "other", "changed", False))
    executor = ToolExecutor(registry, context, ToolLoopHooks(after_tool=rewrite))
    call = ToolCall("c1", "write", {"path": "note.txt", "content": "saved"})
    result = next(event.data["result"] for event in executor.execute([call]) if event.kind == "tool_result")
    assert result.tool_call_id == "c1"
    assert result.name == "write"
    assert result.content == "changed"


def test_max_turns_default_and_override(tmp_path: Path):
    from cli.main import build_parser
    from core.loop import DEFAULT_MAX_TURNS

    assert DEFAULT_MAX_TURNS == 24
    assert LoopConfig().max_turns == 24
    assert Harness(FakeProvider(["ready"]), str(tmp_path)).loop.config.max_turns == 24
    assert Harness(FakeProvider(["ready"]), str(tmp_path), max_turns=3).loop.config.max_turns == 3
    assert build_parser().parse_args(["--max-turns", "24", "hello"]).max_turns == 24


def test_max_turns_rejects_non_positive_values():
    with pytest.raises(ValueError, match="at least 1"):
        LoopConfig(max_turns=0)
