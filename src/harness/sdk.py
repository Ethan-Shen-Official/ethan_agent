"""Default runtime assembly for the Harness facade."""

from __future__ import annotations

from core.context import DefaultContextBuilder
from core.loop import AgentLoop, LoopConfig
from runtime.execution import LocalExecutionEnv
from runtime.permissions import (
    ApprovalHandler,
    PermissionManager,
    PermissionMode,
    WorkspacePermissionPolicy,
)
from tools.base import ToolContext
from tools.executor import ToolExecutor, ToolOutputLimits
from tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, SearchTool, WriteFileTool
from tools.registry import ToolRegistry
from tools.shell import ShellTool

from .hooks import ToolLoopHooks


def create_default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            ListDirTool(),
            SearchTool(),
            ShellTool(),
        ]
    )


def create_hooks(
    hooks: ToolLoopHooks | None,
    *,
    permission_mode: PermissionMode,
    permission_manager: PermissionManager | None,
    approval_handler: ApprovalHandler | None,
) -> ToolLoopHooks:
    policy = permission_manager or WorkspacePermissionPolicy(permission_mode)
    configured = hooks or ToolLoopHooks()
    if configured.permission_manager is not None:
        return configured
    return ToolLoopHooks(
        before_tool=configured.before_tool,
        after_tool=configured.after_tool,
        permission_manager=policy,
        approval_handler=approval_handler,
    )


def create_tool_executor(
    execution_env: LocalExecutionEnv,
    registry: ToolRegistry,
    hooks: ToolLoopHooks,
    output_limits: ToolOutputLimits | None,
) -> ToolExecutor:
    return ToolExecutor(
        registry,
        ToolContext(execution_env),
        hooks,
        output_limits=output_limits,
    )


def create_context_builder(execution_env: LocalExecutionEnv, provider):
    return DefaultContextBuilder(
        str(execution_env.cwd),
        model_name=getattr(getattr(provider, "config", None), "model", "unknown"),
    )


def create_loop(provider, executor, registry: ToolRegistry, max_turns: int, context_builder):
    return AgentLoop(
        provider,
        executor,
        registry.specs(),
        LoopConfig(max_turns=max_turns),
        context_builder,
    )


__all__ = [
    "create_context_builder",
    "create_default_registry",
    "create_hooks",
    "create_loop",
    "create_tool_executor",
]
