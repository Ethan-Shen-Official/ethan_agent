from core.types import Message, ToolResult
from core.errors import SessionError
from harness.app import Harness
from providers.base import FakeProvider
from runtime.compact import (
    CompactConfig,
    TokenLedger,
    estimate_tokens,
    find_cut_point,
    find_valid_cut_points,
    should_compact,
)


class MemorySessionStore:
    def __init__(self):
        self.messages = []
        self._compactions = []

    def append(self, message):
        self.messages.append(message)

    def read(self):
        return list(self.messages)

    def append_compaction(self, metadata):
        self._compactions.append(dict(metadata))

    def compactions(self):
        from types import SimpleNamespace

        return [SimpleNamespace(record_type="compaction", metadata=item) for item in self._compactions]


def test_compact_primitives_preserve_recent_messages():
    messages = [Message.user("goal"), Message.assistant("done"), Message.user("next")]
    assert find_valid_cut_points(messages) == [1, 2]
    assert find_cut_point(messages, 0) == 2
    assert find_cut_point(messages, 100) is None
    tool_tail = [
        Message.user("goal"),
        Message.assistant("done"),
        Message.tool(ToolResult("c1", "read", "result")),
    ]
    assert find_cut_point(tool_tail, 0) == 1
    assert estimate_tokens(messages[0]) == 1
    assert should_compact(10, CompactConfig(context_window=10, reserve_tokens=1)) is True


def test_token_ledger_tracks_appends_and_ranges_incrementally():
    messages = [Message.user("abcd"), Message.assistant("12345")]
    ledger = TokenLedger.from_messages(messages)

    assert ledger.total_tokens == sum(estimate_tokens(message) for message in messages)
    assert ledger.range_tokens(1) == estimate_tokens(messages[1])

    extra = Message.user("new")
    ledger.append(extra)
    assert ledger.message_count == 3
    assert ledger.range_tokens(2) == estimate_tokens(extra)

    ledger.reset([extra])
    assert ledger.message_count == 1
    assert ledger.total_tokens == estimate_tokens(extra)


def test_manual_compact_persists_summary_and_projects_next_context():
    store = MemorySessionStore()
    store.append(Message.user("do the work " * 20))
    store.append(Message.assistant("first answer " * 20))
    provider = FakeProvider(["## Goal\nDo the work", "continued"])
    harness = Harness(
        provider,
        session_store=store,
        compact_config=CompactConfig(context_window=10, reserve_tokens=0, keep_recent_tokens=1),
    )

    result = harness.compact()

    assert result is not None
    assert len(store._compactions) == 1
    snapshot = harness.context_snapshot()
    assert snapshot is not None
    assert snapshot.request.messages[0].content.startswith(
        "The conversation history before this point was compacted"
    )
    assert len(snapshot.request.messages) == 2

    list(harness.prompt("continue"))
    next_snapshot = harness.context_snapshot()
    assert next_snapshot is not None
    assert next_snapshot.request.messages[0].content.startswith(
        "The conversation history before this point was compacted"
    )
    assert next_snapshot.request.messages[-1].content == "continue"


def test_compaction_restores_from_session_store():
    store = MemorySessionStore()
    store.append(Message.user("goal " * 20))
    store.append(Message.assistant("answer " * 20))
    first = Harness(
        FakeProvider(["summary"]),
        session_store=store,
        compact_config=CompactConfig(context_window=10, reserve_tokens=0, keep_recent_tokens=1),
    )
    first.compact()

    resumed = Harness(
        FakeProvider(["resumed"]),
        session_store=store,
        compact_config=CompactConfig(context_window=10, reserve_tokens=0, keep_recent_tokens=1),
    )
    list(resumed.prompt("next"))
    snapshot = resumed.context_snapshot()
    assert snapshot is not None
    assert snapshot.request.messages[0].content.startswith(
        "The conversation history before this point was compacted"
    )


def test_threshold_compact_runs_after_completed_prompt():
    store = MemorySessionStore()
    harness = Harness(
        FakeProvider(["answer", "## Goal\nKeep context"]),
        session_store=store,
        compact_config=CompactConfig(context_window=2, reserve_tokens=0),
    )

    events = list(harness.prompt("goal"))

    assert [event.kind for event in events][-2:] == ["compaction_start", "compaction_end"]
    assert events[-1].data["is_error"] is True
    assert "Nothing to compact (session too small)" in events[-1].data["error"]
    assert len(store._compactions) == 0


def test_compact_repl_command_uses_harness_boundary(capsys):
    from cli.main import handle_repl_command

    class HarnessStub:
        def compact(self):
            from types import SimpleNamespace

            return SimpleNamespace(summarized_count=2, kept_count=3)

    assert handle_repl_command("/compact", HarnessStub()) is True
    assert "summarized 2 messages; kept 3" in capsys.readouterr().out


def test_manual_compact_reports_pi_style_error_for_small_session():
    store = MemorySessionStore()
    harness = Harness(FakeProvider([]), session_store=store)

    try:
        harness.compact()
    except SessionError as exc:
        assert str(exc) == "Nothing to compact (session too small)"
    else:
        raise AssertionError("compact should reject an empty session")


def test_auto_compact_skips_when_context_is_below_range():
    store = MemorySessionStore()
    store.append(Message.user("small"))
    harness = Harness(
        FakeProvider([]),
        session_store=store,
        compact_config=CompactConfig(context_window=100, reserve_tokens=0),
    )

    assert harness.compact(force=False) is None
    assert store._compactions == []


def test_compact_command_formats_small_session_failure(capsys):
    from cli.main import handle_repl_command

    class HarnessStub:
        def compact(self):
            raise SessionError("Nothing to compact (session too small)")

    assert handle_repl_command("/compact", HarnessStub()) is True
    assert capsys.readouterr().out.strip() == "Error: Compaction failed: Nothing to compact (session too small)"
