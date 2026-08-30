"""Application assembly and the stable public Harness facade."""

from __future__ import annotations

from os import PathLike
from pathlib import Path

from core.errors import SessionError
from core.loop import DEFAULT_MAX_TURNS
from runtime.compact import CompactConfig, CompactionContextBuilder, CompactionResult
from runtime.execution import LocalExecutionEnv
from runtime.permissions import (
    ApprovalHandler,
    PermissionManager,
    PermissionMode,
    WorkspacePermissionPolicy,
)
from runtime.session import SessionStore, SessionTreeNode
from tools.executor import ToolOutputLimits

from .agent_session import AgentSession
from .sdk import (
    create_context_builder,
    create_default_registry,
    create_hooks,
    create_loop,
    create_tool_executor,
)
from .hooks import ToolLoopHooks
from .inspection import ContextInspector, ContextSnapshot, InspectingProvider
from .compaction import CompactionService
from .session_manager import SessionManager


class Harness:
    """Compose an :class:`AgentSession` and expose its application facade."""

    def __init__(
        self,
        provider,
        cwd: str = ".",
        max_turns: int = DEFAULT_MAX_TURNS,
        hooks: ToolLoopHooks | None = None,
        session_path: str | PathLike[str] | None = None,
        resume: bool = False,
        session_store: SessionStore | None = None,
        tool_output_limits: ToolOutputLimits | None = None,
        compact_config: CompactConfig | None = None,
        permission_mode: PermissionMode = "default",
        permission_manager: PermissionManager | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.provider = provider
        self.execution_env = LocalExecutionEnv(cwd)
        self.compact_config = compact_config or CompactConfig()
        self.context_inspector = ContextInspector()
        self.session = SessionManager(
            self.execution_env.cwd,
            session_path=session_path,
            resume=resume,
            session_store=session_store,
        )
        self.registry = create_default_registry()
        self.hooks = create_hooks(
            hooks,
            permission_mode=permission_mode,
            permission_manager=permission_manager,
            approval_handler=approval_handler,
        )
        active_permission_manager = self.hooks.permission_manager
        self.permission_policy = (
            active_permission_manager
            if isinstance(active_permission_manager, WorkspacePermissionPolicy)
            else None
        )
        self.tool_executor = create_tool_executor(
            self.execution_env,
            self.registry,
            self.hooks,
            tool_output_limits,
        )
        self.context_builder = CompactionContextBuilder(
            create_context_builder(self.execution_env, provider)
        )
        self.compaction = CompactionService(
            provider,
            self.session,
            self.context_builder,
            self.compact_config,
        )
        self.loop = create_loop(
            InspectingProvider(provider, self.context_inspector),
            self.tool_executor,
            self.registry,
            max_turns,
            self.context_builder,
        )
        self.agent = AgentSession(
            provider=provider,
            execution_env=self.execution_env,
            session=self.session,
            registry=self.registry,
            hooks=self.hooks,
            tool_executor=self.tool_executor,
            context_inspector=self.context_inspector,
            context_builder=self.context_builder,
            compaction=self.compaction,
            loop=self.loop,
        )

    @property
    def state(self):
        return self.agent.state

    @property
    def session_store(self) -> SessionStore:
        return self.session.store

    @property
    def is_running(self) -> bool:
        return self.agent.is_running

    def permission_mode(self) -> PermissionMode:
        if self.permission_policy is None:
            raise SessionError("configured permission manager does not expose a mutable mode")
        return self.permission_policy.mode

    def set_permission_mode(self, mode: PermissionMode) -> None:
        if self.permission_policy is None:
            raise SessionError("configured permission manager does not expose a mutable mode")
        try:
            self.permission_policy.set_mode(mode)
        except ValueError as exc:
            raise SessionError(str(exc)) from exc

    @property
    def session_id(self) -> str:
        return self.agent.session_id

    @property
    def session_path(self) -> Path:
        return self.agent.session_path

    @property
    def session_name(self) -> str | None:
        return self.agent.session_name

    def set_session_name(self, name: str) -> None:
        self.agent.set_session_name(name)

    def session_catalog(self) -> list[dict[str, object]]:
        return self.agent.session_catalog()

    def session_tree(self) -> list[SessionTreeNode]:
        return self.agent.session_tree()

    def new_session(self) -> Path:
        return self.agent.new_session()

    def resume_session(self, identifier: str) -> Path:
        return self.agent.resume_session(identifier)

    def drop_session(self, identifier: str) -> Path:
        return self.agent.drop_session(identifier)

    def context_snapshot(self) -> ContextSnapshot | None:
        return self.agent.context_snapshot()

    def compact(self, *, force: bool = True) -> CompactionResult | None:
        return self.agent.compact(force=force)

    def prompt(self, text: str):
        return self.agent.prompt(text)

    def abort(self) -> None:
        self.agent.abort()

    def checkout(self, message_id: str | None) -> None:
        self.agent.checkout(message_id)

    def resolve_message_id(self, value: str) -> str:
        return self.agent.resolve_message_id(value)

    def rollback(self, message_id: str | None = None) -> None:
        self.agent.rollback(message_id)


__all__ = ["Harness"]
