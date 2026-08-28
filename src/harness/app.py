from __future__ import annotations

from core.context import DefaultContextBuilder
from core.loop import AgentLoop, LoopConfig
from core.state import LoopState
from runtime.execution import LocalExecutionEnv
from runtime.permissions import AllowAllPermissions
from tools.base import ToolContext
from tools.executor import ToolExecutor
from tools.filesystem import ReadFileTool, SearchTool, WriteFileTool
from tools.registry import ToolRegistry
from tools.shell import ExecuteTool


class Harness:
    def __init__(self, provider, cwd: str = ".", max_turns: int = 8) -> None:
        self.state = LoopState()
        self.registry = ToolRegistry([ReadFileTool(), WriteFileTool(), SearchTool(), ExecuteTool()])
        self.execution_env = LocalExecutionEnv(cwd)
        context = ToolContext(self.execution_env, AllowAllPermissions())
        self.tool_executor = ToolExecutor(self.registry, context)
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
