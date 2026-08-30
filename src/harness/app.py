from __future__ import annotations

from os import PathLike

from core.context import DefaultContextBuilder
from core.errors import ProviderError, SessionError
from core.loop import DEFAULT_MAX_TURNS, AgentLoop, LoopConfig
from core.state import LoopState
from core.types import AgentEvent
from runtime.execution import LocalExecutionEnv
from runtime.compact import (
    CompactConfig,
    CompactionContextBuilder,
    CompactionResult,
    TokenLedger,
    estimate_tokens,
    file_operations,
    find_cut_point,
    should_compact,
    summary_message,
    summarize_with_provider,
)
from runtime.permissions import AllowAllPermissions
from runtime.session import JsonlSessionStore, SessionStore, default_session_path, latest_session_path
from tools.base import ToolContext
from tools.executor import ToolExecutor, ToolOutputLimits
from tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, SearchTool, WriteFileTool
from tools.registry import ToolRegistry
from tools.shell import ShellTool
from .hooks import ToolLoopHooks
from .inspection import ContextInspector, ContextSnapshot, InspectingProvider


class Harness:
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
    ) -> None:
        self.execution_env = LocalExecutionEnv(cwd)
        self.provider = provider
        self.compact_config = compact_config or CompactConfig()
        self.context_inspector = ContextInspector()
        if session_store is not None:
            self.session_store = session_store
        else:
            selected_path = session_path
            if selected_path is None and resume:
                selected_path = latest_session_path(self.execution_env.cwd)
            self.session_store = JsonlSessionStore(
                selected_path or default_session_path(self.execution_env.cwd)
            )
        active_snapshot = self._active_snapshot()
        restored_messages = list(active_snapshot.messages) if active_snapshot is not None else self.session_store.read()
        self.state = LoopState(messages=restored_messages)
        self._persisted_message_count = len(restored_messages)
        self._token_ledger = TokenLedger.from_messages(restored_messages)

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
        observed_provider = InspectingProvider(provider, self.context_inspector)
        self.context_builder = CompactionContextBuilder(
            DefaultContextBuilder(
                str(self.execution_env.cwd),
                model_name=getattr(getattr(provider, "config", None), "model", "unknown"),
            )
        )
        self._restore_compaction(active_snapshot)
        self.loop = AgentLoop(
            observed_provider,
            self.tool_executor,
            self.registry.specs(),
            LoopConfig(max_turns=max_turns),
            self.context_builder,
        )

    def context_snapshot(self) -> ContextSnapshot | None:
        """Return the latest exact provider request for read-only diagnostics."""
        return self.context_inspector.snapshot()

    def compact(self, *, force: bool = True) -> CompactionResult | None:
        """Summarize old transcript entries and persist a branch-local marker."""
        messages = list(self.state.messages)
        effective_start = self.context_builder.summarized_count
        working_messages = messages[effective_start:]
        tokens_before = self._projected_token_count(messages)
        if not force and not should_compact(tokens_before, self.compact_config):
            return None
        cut = find_cut_point(working_messages, self.compact_config.keep_recent_tokens)
        if cut is None or cut <= 0 or cut >= len(working_messages):
            raise SessionError("not enough complete history to compact")
        old_messages = working_messages[:cut]
        previous_record = self._latest_compaction_record()
        previous = (previous_record.metadata or {}).get("summary") if previous_record else None
        if not isinstance(previous, str):
            previous = None
        summary = summarize_with_provider(self.provider, old_messages, previous)
        details = file_operations(old_messages)
        if previous_record:
            old_details = (previous_record.metadata or {}).get("details") or {}
            details = {
                "read_files": sorted(set(old_details.get("read_files", [])) | set(details["read_files"])),
                "modified_files": sorted(set(old_details.get("modified_files", [])) | set(details["modified_files"])),
            }
        absolute_cut = effective_start + cut
        result = CompactionResult(
            summary,
            tokens_before,
            absolute_cut,
            len(messages) - absolute_cut,
            details,
        )
        append = getattr(self.session_store, "append_compaction", None)
        if append is None:
            raise SessionError("configured session store does not support compaction")
        append(result.metadata())
        self.context_builder.set_summary(result.summary, result.summarized_count)
        # Make /show_context immediately reflect the next real model context.
        self.context_inspector.capture(self.loop.prepare_context(self.state))
        return result

    def _latest_compaction_record(self):
        snapshot = self._active_snapshot()
        if snapshot is not None:
            return snapshot.latest_compaction
        records = getattr(self.session_store, "compactions", lambda: [])()
        return records[-1] if records else None

    def _active_snapshot(self):
        snapshot = getattr(self.session_store, "active_snapshot", None)
        return snapshot() if snapshot is not None else None

    def _restore_compaction(self, snapshot=None) -> None:
        self.context_builder.set_summary(None, 0)
        records = snapshot.compactions if snapshot is not None else getattr(self.session_store, "compactions", lambda: [])()
        if not records:
            return
        metadata = records[-1].metadata or {}
        summary = metadata.get("summary")
        summarized_count = metadata.get("summarized_count", 0)
        if isinstance(summary, str) and isinstance(summarized_count, int):
            self.context_builder.set_summary(summary, summarized_count)

    def _sync_token_ledger(self, messages: list) -> None:
        if self._token_ledger.message_count != len(messages):
            self._token_ledger.reset(messages)

    def _projected_token_count(self, messages: list | None = None) -> int:
        current = self.state.messages if messages is None else messages
        self._sync_token_ledger(current)
        start = min(max(self.context_builder.summarized_count, 0), self._token_ledger.message_count)
        summary_tokens = 0
        if self.context_builder.summary and start > 0:
            summary_tokens = estimate_tokens(summary_message(self.context_builder.summary))
        return summary_tokens + self._token_ledger.range_tokens(start)

    def prompt(self, text: str):
        for event in self.loop.run(text, self.state):
            self._persist_new_messages()
            yield event
        self._persist_new_messages()
        if self.state.stop_reason == "completed":
            yield from self._auto_compact()

    def _auto_compact(self):
        """Run threshold compaction after a completed turn, outside the core loop."""
        tokens = self._projected_token_count()
        if not should_compact(tokens, self.compact_config):
            return
        yield AgentEvent("compaction_start", {"reason": "threshold", "tokens": tokens})
        try:
            result = self.compact(force=False)
        except (ProviderError, SessionError) as exc:
            yield AgentEvent("compaction_end", {"error": str(exc), "is_error": True})
            return
        if result is not None:
            yield AgentEvent(
                "compaction_end",
                {"summary": result.summary, "tokens_before": result.tokens_before},
            )

    def _persist_new_messages(self) -> None:
        while self._persisted_message_count < len(self.state.messages):
            message = self.state.messages[self._persisted_message_count]
            self.session_store.append(message)
            self._token_ledger.append(message)
            self._persisted_message_count += 1

    def abort(self) -> None:
        self.state.request_cancel()

    def checkout(self, message_id: str | None) -> None:
        """Switch the active session branch and reload LoopState from it."""
        checkout = getattr(self.session_store, "checkout", None)
        if checkout is None:
            raise SessionError("Configured session store does not support branches")
        checkout(message_id)
        active_snapshot = self._active_snapshot()
        self.state.messages = list(active_snapshot.messages) if active_snapshot is not None else self.session_store.read()
        self.state.turn_count = 0
        self.state.stop_reason = None
        self.state.recovery_failures = 0
        self._persisted_message_count = len(self.state.messages)
        self._token_ledger.reset(self.state.messages)
        self._restore_compaction(active_snapshot)

    def resolve_message_id(self, value: str) -> str:
        resolver = getattr(self.session_store, "resolve_message_id", None)
        if resolver is None:
            raise SessionError("Configured session store does not support message lookup")
        return resolver(value)

    def rollback(self, message_id: str | None = None) -> None:
        rollback = getattr(self.session_store, "rollback", None)
        if rollback is None:
            raise SessionError("Configured session store does not support rollback")
        rollback(message_id)
        active_snapshot = self._active_snapshot()
        self.state.messages = list(active_snapshot.messages) if active_snapshot is not None else self.session_store.read()
        self.state.turn_count = 0
        self.state.stop_reason = None
        self.state.recovery_failures = 0
        self._persisted_message_count = len(self.state.messages)
        self._token_ledger.reset(self.state.messages)
        self._restore_compaction(active_snapshot)
