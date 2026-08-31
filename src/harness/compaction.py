"""Runtime orchestration for transcript compaction."""

from __future__ import annotations

from core.errors import SessionError
from runtime.compact import (
    CompactConfig,
    CompactionContextBuilder,
    CompactionResult,
    estimate_tokens,
    file_operations,
    find_cut_point,
    should_compact,
    summary_message,
    summarize_with_provider,
)

from .session_manager import SessionManager


class CompactionService:
    """Coordinate provider summarization, persistence and context projection."""

    def __init__(
        self,
        provider,
        session: SessionManager,
        context_builder: CompactionContextBuilder,
        config: CompactConfig,
    ) -> None:
        self.provider = provider
        self.session = session
        self.context_builder = context_builder
        self.config = config

    def restore(self, snapshot=None) -> None:
        self.context_builder.set_summary(None, 0)
        active = snapshot if snapshot is not None else self.session.active_snapshot()
        records = active.compactions if active is not None else getattr(self.session.store, "compactions", lambda: [])()
        if not records:
            return
        metadata = records[-1].metadata or {}
        summary = metadata.get("summary")
        summarized_count = metadata.get("summarized_count", 0)
        if isinstance(summary, str) and isinstance(summarized_count, int):
            self.context_builder.set_summary(summary, summarized_count)

    def projected_token_count(self, messages: list | None = None) -> int:
        current = self.session.state.messages if messages is None else messages
        ledger = self.session.token_ledger
        if ledger.message_count != len(current):
            ledger.reset(current)
        start = min(max(self.context_builder.summarized_count, 0), ledger.message_count)
        summary_tokens = 0
        if self.context_builder.summary and start > 0:
            summary_tokens = estimate_tokens(summary_message(self.context_builder.summary))
        return summary_tokens + ledger.range_tokens(start)

    def compact(self, *, force: bool = True) -> CompactionResult | None:
        messages = list(self.session.state.messages)
        effective_start = self.context_builder.summarized_count
        working_messages = messages[effective_start:]
        tokens_before = self.projected_token_count(messages)
        exceeds_range = tokens_before > self.config.context_window - self.config.reserve_tokens
        if force and not exceeds_range:
            raise SessionError("Nothing to compact (session too small)")
        if not force and not should_compact(tokens_before, self.config):
            return None

        cut = find_cut_point(working_messages, self.config.keep_recent_tokens)
        if cut is None or cut <= 0 or cut >= len(working_messages):
            # Manual compaction should report the same actionable failure as
            # Pi when the transcript cannot produce an older summary region.
            # Automatic threshold compaction reaches this branch only after a
            # real threshold hit, so it remains an error rather than silently
            # writing an empty summary.
            raise SessionError("Nothing to compact (session too small)")
        old_messages = working_messages[:cut]
        previous_record = self.latest_record()
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
        append = getattr(self.session.store, "append_compaction", None)
        if append is None:
            raise SessionError("configured session store does not support compaction")
        append(result.metadata())
        self.context_builder.set_summary(result.summary, result.summarized_count)
        return result

    def latest_record(self):
        snapshot = self.session.active_snapshot()
        if snapshot is not None:
            return snapshot.latest_compaction
        records = getattr(self.session.store, "compactions", lambda: [])()
        return records[-1] if records else None


__all__ = ["CompactionService"]
