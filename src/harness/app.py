from __future__ import annotations

from os import PathLike

from core.context import DefaultContextBuilder
from core.errors import SessionError
from core.loop import DEFAULT_MAX_TURNS, AgentLoop, LoopConfig
from core.state import LoopState
from runtime.execution import LocalExecutionEnv
from runtime.permissions import AllowAllPermissions
from runtime.session import JsonlSessionStore, SessionStore, default_session_path
from tools.base import ToolContext
from tools.executor import ToolExecutor, ToolOutputLimits
from tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, SearchTool, WriteFileTool
from tools.registry import ToolRegistry
from tools.shell import ShellTool
from .hooks import ToolLoopHooks


class Harness:
    def __init__(
        self,
        provider,
        cwd: str = ".",
        max_turns: int = DEFAULT_MAX_TURNS,
        hooks: ToolLoopHooks | None = None,
        session_path: str | PathLike[str] | None = None,
        session_store: SessionStore | None = None,
        tool_output_limits: ToolOutputLimits | None = None,
    ) -> None:
        self.execution_env = LocalExecutionEnv(cwd)
        self.session_store = session_store or JsonlSessionStore(
            session_path or default_session_path(self.execution_env.cwd)
        )
        restored_messages = self.session_store.read()
        self.state = LoopState(messages=restored_messages)
        self._persisted_message_count = len(restored_messages)

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
        context = ToolContext(self.execution_env, AllowAllPermissions())
        self.hooks = hooks or ToolLoopHooks()
        self.tool_executor = ToolExecutor(
            self.registry,
            context,
            self.hooks,
            output_limits=tool_output_limits,
        )
        self.loop = AgentLoop(
            provider,
            self.tool_executor,
            self.registry.specs(),
            LoopConfig(max_turns=max_turns),
            DefaultContextBuilder(
                str(self.execution_env.cwd),
                model_name=getattr(getattr(provider, "config", None), "model", "unknown"),
            ),
        )

    def prompt(self, text: str):
        for event in self.loop.run(text, self.state):
            self._persist_new_messages()
            yield event
        self._persist_new_messages()

    def _persist_new_messages(self) -> None:
        while self._persisted_message_count < len(self.state.messages):
            self.session_store.append(self.state.messages[self._persisted_message_count])
            self._persisted_message_count += 1

    def abort(self) -> None:
        self.state.request_cancel()

    def checkout(self, message_id: str | None) -> None:
        """Switch the active session branch and reload LoopState from it."""
        checkout = getattr(self.session_store, "checkout", None)
        if checkout is None:
            raise SessionError("Configured session store does not support branches")
        checkout(message_id)
        self.state.messages = self.session_store.read()
        self.state.turn_count = 0
        self.state.stop_reason = None
        self.state.recovery_failures = 0
        self._persisted_message_count = len(self.state.messages)

    rollback = checkout
