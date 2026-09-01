"""Default runtime assembly for the Harness facade."""

from __future__ import annotations

from dataclasses import replace

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
from tools.details import ToolDetailsStore
from tools.executor import ToolExecutor, ToolOutputLimits
from tools.filesystem import EditTool, FindTool, GrepTool, LsTool, ReadTool, WriteTool
from tools.registry import ToolRegistry
from tools.process import BashTool, PowerShellTool

from .hooks import ToolLoopHooks


TOOL_SELECTION_GUIDANCE = """Tool selection rules:
- For directory inspection, use ls first.
- For locating files or directories by name or glob, use find.
- For searching file contents, use grep.
- For reading a known file, use read (use offset/limit for large files).
- Use bash or powershell only when the dedicated read/write/edit tools cannot express the task.
- Do not use bash or powershell for ls/dir, find, grep/rg, cat/type, or equivalent read-only tasks.
- read, ls, find, and grep are read-only and do not require permission; write, edit, bash, and powershell may require permission.
"""


class ToolSelectionContextBuilder:
    """Append tool-choice guidance without changing the core context contract."""

    def __init__(self, base: DefaultContextBuilder) -> None:
        self.base = base

    def __getattr__(self, name):
        # Preserve the useful inspection attributes of DefaultContextBuilder
        # for embedders while keeping the guidance outside core/context.py.
        return getattr(self.base, name)

    def build(self, state, tools, system_prompt):
        request = self.base.build(state, tools, system_prompt)
        return replace(
            request,
            system_prompt=f"{request.system_prompt}\n\n{TOOL_SELECTION_GUIDANCE.strip()}",
        )


def create_default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ReadTool(),
            WriteTool(),
            EditTool(),
            LsTool(),
            FindTool(),
            GrepTool(),
            BashTool(),
            PowerShellTool(),
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
        ToolContext(execution_env, details_store=ToolDetailsStore()),
        hooks,
        output_limits=output_limits,
    )


def create_context_builder(execution_env: LocalExecutionEnv, provider):
    return ToolSelectionContextBuilder(
        DefaultContextBuilder(
            str(execution_env.cwd),
            model_name=getattr(getattr(provider, "config", None), "model", "unknown"),
        )
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
