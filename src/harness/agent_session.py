from __future__ import annotations

from pathlib import Path

from core.errors import ProviderError,SessionError
from core.loop import AgentLoop
from core.types import AgentEvent
from runtime.execution import LocalExecutionEnv
from runtime.compact import (
    CompactionContextBuilder,
    CompactionResult,
    should_compact,
)
from runtime.session import (
    SessionStore,
    SessionTreeNode,
)
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from .compaction import CompactionService
from .hooks import ToolLoopHooks
from .inspection import ContextInspector, ContextSnapshot
from .session_manager import SessionManager


class AgentSession:
    def __init__(
        self,
        *,
        provider,
        execution_env: LocalExecutionEnv,
        session: SessionManager,
        registry: ToolRegistry,
        hooks: ToolLoopHooks,
        tool_executor: ToolExecutor,
        context_inspector: ContextInspector,
        context_builder: CompactionContextBuilder,
        compaction: CompactionService,
        loop: AgentLoop,
    ) -> None:
        self.provider = provider
        self.execution_env = execution_env
        self.session = session
        self.registry = registry
        self.hooks = hooks
        self.tool_executor = tool_executor
        self.context_inspector = context_inspector
        self.context_builder = context_builder
        self.compaction = compaction
        self.loop = loop
        self.compaction.restore()

    @property
    def state(self):
        return self.session.state

    @property
    def session_store(self) -> SessionStore:
        return self.session.store

    @property
    def session_id(self) -> str:
        """Return the identifier of the currently active session."""
        return self.session.session_id

    @property
    def session_path(self) -> Path:
        """Return the persisted file of the currently active session."""
        return self.session.session_path

    @property
    def session_name(self) -> str | None:
        """Return the current session's display name, when supported."""
        return self.session.session_name

    def set_session_name(self, name: str) -> None:
        """Persist a display name without adding anything to model context."""
        self.session.set_session_name(name)

    def session_catalog(self) -> list[dict[str, object]]:
        """Return lightweight metadata for the workspace session selector."""
        return self.session.session_catalog()

    def session_tree(self) -> list[SessionTreeNode]:
        """Return a stable, read-only view of the current session tree."""
        return self.session.session_tree()

    def new_session(self) -> Path:
        """Create and activate a new empty session without changing the Loop."""
        path = self.session.new_session()
        self.context_inspector.clear()
        self.compaction.restore()
        return path

    def resume_session(self, identifier: str) -> Path:
        """Activate a persisted session selected by id, stem, or unique prefix."""
        path = self.session.resume_session(identifier)
        self.context_inspector.clear()
        self.compaction.restore()
        return path

    def drop_session(self, identifier: str) -> Path:
        """Delete a non-active managed session by id, stem, or unique prefix."""
        return self.session.drop_session(identifier)

    def context_snapshot(self) -> ContextSnapshot | None:
        """Return the latest exact provider request for read-only diagnostics."""
        return self.context_inspector.snapshot()

    def compact(self, *, force: bool = True) -> CompactionResult | None:
        """Summarize old transcript entries and persist a branch-local marker."""
        result = self.compaction.compact(force=force)
        # Make /show_context immediately reflect the next real model context.
        if result is not None:
            self.context_inspector.capture(self.loop.prepare_context(self.state))
        return result

    def prompt(self, text: str):
        for event in self.loop.run(text, self.state):
            self.session.persist_pending()
            yield event
        self.session.persist_pending()
        if self.state.stop_reason == "completed":
            yield from self._auto_compact()

    def _auto_compact(self):
        """Run threshold compaction after a completed turn, outside the core loop."""
        tokens = self.compaction.projected_token_count()
        if not should_compact(tokens, self.compaction.config):
            return
        yield AgentEvent("compaction_start", {"reason": "threshold", "tokens": tokens})
        try:
            result = self.compaction.compact(force=False)
        except (ProviderError, SessionError) as exc:
            yield AgentEvent("compaction_end", {"error": str(exc), "is_error": True})
            return
        if result is not None:
            self.context_inspector.capture(self.loop.prepare_context(self.state))
            yield AgentEvent(
                "compaction_end",
                {"summary": result.summary, "tokens_before": result.tokens_before},
            )

    def abort(self) -> None:
        self.state.request_cancel()

    def checkout(self, message_id: str | None) -> None:
        """Switch the active session branch and reload LoopState from it."""
        self.session.checkout(message_id)
        self.context_inspector.clear()
        self.compaction.restore()

    def resolve_message_id(self, value: str) -> str:
        return self.session.resolve_message_id(value)

    def rollback(self, message_id: str | None = None) -> None:
        self.session.rollback(message_id)
        self.context_inspector.clear()
        self.compaction.restore()


__all__ = ["AgentSession"]
