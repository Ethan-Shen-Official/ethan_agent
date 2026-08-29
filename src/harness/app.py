from __future__ import annotations

from core.context import DefaultContextBuilder
from core.loop import DEFAULT_MAX_TURNS, AgentLoop, LoopConfig
from core.state import LoopState
from runtime.execution import LocalExecutionEnv
from runtime.permissions import AllowAllPermissions
from tools.base import ToolContext
from tools.executor import ToolExecutor
from tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, SearchTool, WriteFileTool
from tools.registry import ToolRegistry
from tools.shell import ShellTool
from .hooks import ToolLoopHooks


class Harness:
    def __init__(self, provider, cwd: str = ".", max_turns: int = DEFAULT_MAX_TURNS, hooks: ToolLoopHooks | None = None) -> None:
        self.state = LoopState()
        self.registry = ToolRegistry(
            [
                ReadFileTool(),
                WriteFileTool(),
                EditFileTool(),
                ListDirTool(),
                SearchTool(),
                ShellTool(),
            ]
        )
        self.execution_env = LocalExecutionEnv(cwd)
        context = ToolContext(self.execution_env, AllowAllPermissions())
        self.hooks = hooks or ToolLoopHooks()
        self.tool_executor = ToolExecutor(self.registry, context, self.hooks)
        self.loop = AgentLoop(
            provider,
            self.tool_executor,
            self.registry.specs(),
            LoopConfig(max_turns=max_turns),
            DefaultContextBuilder(str(self.execution_env.cwd), model_name=getattr(getattr(provider, "config", None), "model", "unknown")),
        )

    def prompt(self, text: str):
        return self.loop.run(text, self.state)

    def abort(self) -> None:
        self.state.request_cancel()
