from pathlib import Path

from core.hooks import ToolHookDecision
from core.types import ToolCall
from harness.app import Harness
from harness.hooks import ToolLoopHooks
from providers.base import FakeProvider
from runtime.execution import LocalExecutionEnv
from runtime.permissions import PermissionDecision, WorkspacePermissionPolicy
from tools.base import ToolContext
from tools.executor import ToolExecutor
from tools.filesystem import ReadFileTool, WriteFileTool
from tools.registry import ToolRegistry


def _result(executor: ToolExecutor, call: ToolCall):
    return next(event.data["result"] for event in executor.execute([call]) if event.kind == "tool_result")


def test_default_policy_allows_reads_and_asks_for_writes(tmp_path: Path):
    policy = WorkspacePermissionPolicy()
    assert policy.check("read_file", {"path": "a.txt"}).behavior == "allow"
    assert policy.check("write", {"path": "a.txt", "content": "x"}).behavior == "ask"


def test_policy_exposes_only_three_modes():
    for mode in ("default", "accept_edits", "bypass_permissions"):
        WorkspacePermissionPolicy(mode)

    try:
        WorkspacePermissionPolicy("plan")
    except ValueError:
        pass
    else:
        raise AssertionError("removed permission mode was accepted")


def test_before_pipeline_denies_ask_without_approval(tmp_path: Path):
    registry = ToolRegistry([WriteFileTool()])
    hooks = ToolLoopHooks(
        permission_manager=WorkspacePermissionPolicy(),
        approval_handler=lambda request: False,
    )
    executor = ToolExecutor(registry, ToolContext(LocalExecutionEnv(tmp_path), None), hooks)

    result = _result(executor, ToolCall("call", "write", {"path": "blocked.txt", "content": "no"}))

    assert result.is_error is True
    assert "editing files requires approval" in result.content
    assert not (tmp_path / "blocked.txt").exists()


def test_before_pipeline_allows_after_approval(tmp_path: Path):
    registry = ToolRegistry([WriteFileTool()])
    hooks = ToolLoopHooks(
        permission_manager=WorkspacePermissionPolicy(),
        approval_handler=lambda request: True,
    )
    executor = ToolExecutor(registry, ToolContext(LocalExecutionEnv(tmp_path), None), hooks)

    result = _result(executor, ToolCall("call", "write", {"path": "allowed.txt", "content": "yes"}))

    assert result.is_error is False
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "yes"


def test_protected_metadata_is_denied_even_in_bypass_mode(tmp_path: Path):
    policy = WorkspacePermissionPolicy("bypass_permissions")
    decision = policy.check("write", {"path": ".agent/settings.json", "content": "bad"})
    assert decision == PermissionDecision("deny", decision.reason, "protected-path")


def test_shell_commands_cannot_remove_protected_metadata():
    policy = WorkspacePermissionPolicy("bypass_permissions")
    decision = policy.check("exe", {"cmd": "rmdir /s /q .agent .git"})
    assert decision == PermissionDecision("deny", decision.reason, "protected-path")
    assert policy.check("exe", {"cmd": "del .gitignore"}).behavior == "allow"


def test_workspace_wide_delete_blacklist_applies_to_every_mode():
    commands = (
        "rm -rf .",
        "rm --recursive --force ./",
        "rmdir /s /q .",
        "del /s /q *.*",
        "Remove-Item -Recurse -Force .",
        "find . -delete",
        "git clean -fdx",
    )
    for mode in ("default", "accept_edits", "bypass_permissions"):
        policy = WorkspacePermissionPolicy(mode)
        for command in commands:
            decision = policy.check("exe", {"cmd": command})
            assert decision.behavior == "deny", (mode, command, decision)
            assert decision.rule == "destructive-command"


def test_custom_argument_replacement_is_checked_by_policy(tmp_path: Path):
    registry = ToolRegistry([WriteFileTool()])
    hooks = ToolLoopHooks(
        before_tool=lambda context: ToolHookDecision(
            action="replace_arguments",
            arguments={"path": ".agent/blocked", "content": "no"},
        ),
        permission_manager=WorkspacePermissionPolicy("bypass_permissions"),
        approval_handler=lambda request: True,
    )
    executor = ToolExecutor(registry, ToolContext(LocalExecutionEnv(tmp_path), None), hooks)

    result = _result(executor, ToolCall("call", "write", {"path": "safe.txt", "content": "original"}))

    assert result.is_error is True
    assert not (tmp_path / ".agent" / "blocked").exists()


def test_permission_policy_can_be_customized_without_executor_changes(tmp_path: Path):
    class DenyReads:
        def check(self, tool_name, arguments):
            return PermissionDecision("deny", "read policy")

    registry = ToolRegistry([ReadFileTool()])
    hooks = ToolLoopHooks(permission_manager=DenyReads())
    executor = ToolExecutor(registry, ToolContext(LocalExecutionEnv(tmp_path), None), hooks)
    result = _result(executor, ToolCall("call", "read_file", {"path": "missing.txt"}))

    assert result.is_error is True
    assert "read policy" in result.content


def test_harness_permission_mode_switches_for_subsequent_calls(tmp_path: Path):
    harness = Harness(FakeProvider(["done"]), str(tmp_path))

    assert harness.permission_mode() == "default"
    assert harness.permission_policy is not None
    assert harness.permission_policy.check("write", {"path": "a.txt"}).behavior == "ask"

    harness.set_permission_mode("accept_edits")
    assert harness.permission_mode() == "accept_edits"
    assert harness.permission_policy.check("write", {"path": "a.txt"}).behavior == "allow"

    harness.set_permission_mode("default")
    assert harness.permission_mode() == "default"
    assert harness.permission_policy.check("write", {"path": "a.txt"}).behavior == "ask"


def test_cli_permission_mode_command(capsys, tmp_path: Path):
    from cli.main import handle_repl_command

    harness = Harness(FakeProvider(["done"]), str(tmp_path))
    assert handle_repl_command("/permission_mode", harness) is True
    assert "default" in capsys.readouterr().out

    assert handle_repl_command("/permission_mode bypass_permissions", harness) is True
    assert "switched to bypass_permissions" in capsys.readouterr().out
    assert harness.permission_mode() == "bypass_permissions"

    assert handle_repl_command("/permission_mode invalid", harness) is True
    assert "usage:" in capsys.readouterr().out
    assert harness.permission_mode() == "bypass_permissions"

    assert handle_repl_command("/permission_mode default extra", harness) is True
    assert "usage:" in capsys.readouterr().out


def test_cli_permission_mode_short_aliases(capsys, tmp_path: Path):
    from cli.main import handle_repl_command

    harness = Harness(FakeProvider(["done"]), str(tmp_path))
    for alias, mode in (("d", "default"), ("e", "accept_edits"), ("b", "bypass_permissions")):
        assert handle_repl_command(f"/perm {alias}", harness) is True
        assert harness.permission_mode() == mode
        assert f"switched to {mode}" in capsys.readouterr().out
