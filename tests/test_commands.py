from pathlib import Path

from cli.commands import format_help, format_tree
from cli.main import handle_repl_command
from harness.app import Harness
from providers.base import FakeProvider
from runtime.session import JsonlSessionStore
from runtime.session.types import SessionTreeNode


def test_help_lists_registered_commands_and_arguments():
    output = format_help()
    for command in ("/help", "/new", "/name", "/resume", "/drop", "/tree", "/checkout", "/rollback", "/perm", "/exit"):
        assert command in output
    assert "/resume [session-id]" in output
    assert "/rollback [message-id]" in output
    assert "default, accept_edits, bypass_permissions" in output


def test_new_and_resume_switch_active_session(tmp_path: Path, capsys):
    harness = Harness(FakeProvider(["first", "second"]), str(tmp_path))
    list(harness.prompt("first prompt"))
    original_path = harness.session_path
    original_id = harness.session_id
    original_messages = list(harness.state.messages)

    assert handle_repl_command("/new", harness) is True
    new_path = harness.session_path
    assert new_path != original_path
    assert harness.state.messages == []
    assert "[new] session:" in capsys.readouterr().out

    list(harness.prompt("second prompt"))
    assert [message.content for message in harness.state.messages] == ["second prompt", "second"]
    new_messages = list(harness.state.messages)

    assert handle_repl_command(f"/res {original_id[:8]}", harness) is True
    assert harness.session_path == original_path
    assert harness.session_id == original_id
    assert harness.state.messages == original_messages
    assert "[resume]" in capsys.readouterr().out

    # The new session retained its own transcript and head while the active
    # Harness moved back to the original session.
    restored_new = JsonlSessionStore(new_path)
    assert restored_new.read() == new_messages


def test_resume_requires_an_existing_session(tmp_path: Path, capsys):
    harness = Harness(FakeProvider(["done"]), str(tmp_path))
    assert handle_repl_command("/resume missing", harness) is True
    assert "Unknown session" in capsys.readouterr().out


def test_tree_shows_node_ids_roles_and_active_path(tmp_path: Path, capsys):
    harness = Harness(FakeProvider(["answer"]), str(tmp_path))
    list(harness.prompt("inspect tree"))

    assert handle_repl_command("/tree", harness) is True
    output = capsys.readouterr().out
    assert "[tree]" in output
    assert "user" in output
    assert "assistant" in output
    assert "* active leaf" in output
    assert "* " in output

    assert handle_repl_command("/tree extra", harness) is True
    assert "usage: /tree" in capsys.readouterr().out


def test_format_tree_bounds_deep_indentation_and_terminal_width():
    nodes = [
        SessionTreeNode(
            message_id="root-node-1234",
            parent_id=None,
            record_type="message",
            role="user",
            preview="root",
            depth=0,
            children_ids=("deep-node-5678",),
            is_active=True,
            is_leaf=False,
        ),
        SessionTreeNode(
            message_id="deep-node-5678",
            parent_id="root-node-1234",
            record_type="message",
            role="assistant",
            preview="a very long preview that must be clipped at the terminal boundary",
            depth=30,
            children_ids=(),
            is_active=True,
            is_leaf=True,
        ),
    ]

    lines = format_tree(nodes, width=52, max_indent=4)
    assert len(lines) == 2
    assert all(len(line) <= 52 for line in lines)
    assert "[d30]" in lines[1]
    assert "..." in lines[1]
    assert lines[1].startswith("* ")


def test_name_is_persisted_as_metadata_and_not_model_message(tmp_path: Path, capsys):
    harness = Harness(FakeProvider(["done"]), str(tmp_path))
    assert handle_repl_command("/name", harness) is True
    assert "(unnamed)" in capsys.readouterr().out

    assert handle_repl_command("/name multi file task", harness) is True
    assert harness.session_name == "multi file task"
    assert [message.content for message in harness.state.messages] == []
    output = capsys.readouterr().out
    assert "multi file task" in output

    restored = JsonlSessionStore(harness.session_path)
    assert restored.get_session_name() == "multi file task"
    assert restored.read() == []

    assert handle_repl_command("/name -", harness) is True
    assert harness.session_name is None
    assert capsys.readouterr().out.endswith("(unnamed)\n")


def test_resume_without_argument_selects_by_number(tmp_path: Path, monkeypatch, capsys):
    harness = Harness(FakeProvider(["first", "second"]), str(tmp_path))
    list(harness.prompt("first prompt"))
    original_path = harness.session_path
    assert handle_repl_command("/name first-session", harness) is True
    capsys.readouterr()

    assert handle_repl_command("/new", harness) is True
    list(harness.prompt("second prompt"))
    assert handle_repl_command("/name second-session", harness) is True
    capsys.readouterr()

    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert handle_repl_command("/resume", harness) is True
    output = capsys.readouterr().out
    assert "Available sessions:" in output
    assert "[resume] second-session" in output or "[resume] first-session" in output

    # The catalog is newest-first, so selecting entry 2 returns the original.
    assert harness.session_path == original_path
    assert harness.session_name == "first-session"
    assert [message.content for message in harness.state.messages] == ["first prompt", "first"]


def test_drop_deletes_selected_non_current_session_and_preserves_current(
    tmp_path: Path, monkeypatch, capsys
):
    harness = Harness(FakeProvider(["first", "second"]), str(tmp_path))
    list(harness.prompt("first prompt"))
    first_path = harness.session_path
    first_id = harness.session_id

    handle_repl_command("/new", harness)
    list(harness.prompt("second prompt"))
    second_path = harness.session_path
    second_messages = list(harness.state.messages)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda _: "y")

    assert handle_repl_command(f"/drop {first_id[:10]}", harness) is True
    assert not first_path.exists()
    assert not first_path.with_suffix(".head").exists()
    assert harness.session_path == second_path
    assert harness.state.messages == second_messages

    assert handle_repl_command("/drop", harness) is True
    assert second_path.exists()
    assert harness.session_store is not None
    assert harness.session_path == second_path
    assert harness.state.messages == second_messages
    current_id = harness.session_id
    assert handle_repl_command(f"/drop {current_id[:10]}", harness) is True
    assert second_path.exists()
    output = capsys.readouterr().out
    assert "[drop] deleted:" in output
    assert "Cannot drop the active session" in output


def test_drop_requires_confirmation(tmp_path: Path, monkeypatch, capsys):
    harness = Harness(FakeProvider(["done"]), str(tmp_path))
    path = harness.session_path
    list(harness.prompt("done"))
    session_id = harness.session_id
    monkeypatch.setattr("builtins.input", lambda _: "n")

    assert handle_repl_command(f"/drop {session_id[:10]}", harness) is True
    assert path.exists()
    assert harness.session_path == path
    assert "[drop] cancelled" in capsys.readouterr().out


def test_drop_requires_an_explicit_session_id(tmp_path: Path, capsys):
    harness = Harness(FakeProvider(["done"]), str(tmp_path))
    current = harness.session_path
    assert handle_repl_command("/drop", harness) is True
    assert current.exists()
    assert "usage: /drop <session-id>" in capsys.readouterr().out
