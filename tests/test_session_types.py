from typing import get_args

import pytest

from core.errors import SessionError
from core.types import Message
from runtime.session import JsonlSessionStore, RecordType, SessionRecord


def _record(*, message=None, record_type="message", metadata=None):
    return SessionRecord(
        version=1,
        session_id="session",
        message_id="node",
        parent_id=None,
        operation_id="operation",
        message=message,
        record_type=record_type,
        metadata=metadata,
    )


def test_record_type_is_a_closed_runtime_discriminator():
    assert get_args(RecordType) == ("message", "compaction")
    assert _record(message=Message.user("hello")).record_type == "message"
    assert _record(record_type="compaction", metadata={"summary": "done"}).message is None


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"record_type": "message", "message": None}, "Message records must contain a message"),
        ({"record_type": "compaction", "message": Message.user("wrong")}, "Compaction records cannot contain a message"),
        ({"record_type": "unknown", "message": None}, "Unknown session record type: unknown"),
    ],
)
def test_record_payload_matches_record_type(kwargs, message):
    with pytest.raises(ValueError, match=message):
        _record(**kwargs)


def test_jsonl_store_rejects_unknown_record_type():
    store = object.__new__(JsonlSessionStore)
    with pytest.raises(SessionError, match="Unknown session record type: branch_summary"):
        store._parse_records(
            [
                {
                    "version": 1,
                    "type": "branch_summary",
                    "session_id": "session",
                    "message_id": "node",
                    "parent_id": None,
                    "operation_id": "operation",
                    "metadata": {},
                }
            ]
        )


def test_active_snapshot_reuses_one_tree_path_for_derived_views():
    class CountingTree:
        def __init__(self, path):
            self.path = path
            self.calls = 0

        def current_path(self):
            self.calls += 1
            return self.path

    records = [
        _record(message=Message.user("hello")),
        _record(record_type="compaction", metadata={"summary": "checkpoint"}),
    ]
    store = object.__new__(JsonlSessionStore)
    tree = CountingTree(records)
    store._tree = tree
    store._active_snapshot = None

    assert store.read() == [records[0].message]
    assert store.compactions() == [records[1]]
    assert tree.calls == 1
