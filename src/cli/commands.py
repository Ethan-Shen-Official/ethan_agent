"""REPL command registry and handlers shared by CLI and future TUI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import shutil
import sys
from typing import TYPE_CHECKING

from core.errors import ProviderError, SessionError
from harness.inspection import format_context_snapshot
from runtime.permissions import PERMISSION_MODES

if TYPE_CHECKING:
    from harness.app import Harness


PERMISSION_MODE_ALIASES = {
    "d": "default",
    "e": "accept_edits",
    "b": "bypass_permissions",
}


@dataclass(frozen=True)
class CommandSpec:
    """Metadata used for dispatch, help output, and future TUI palettes."""

    name: str
    aliases: tuple[str, ...]
    usage: str
    description: str

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("/help", ("/h", "/?"), "/help [command]", "Show all commands or one command's details."),
    CommandSpec("/new", ("/n",), "/new", "Create and activate a new empty session."),
    CommandSpec("/name", ("/nm",), "/name [name]", "Show, set, or clear the current session display name."),
    CommandSpec("/resume", ("/res",), "/resume [session-id]", "List sessions or switch by id, filename, or unique prefix."),
    CommandSpec("/drop", (), "/drop <session-id>", "Permanently delete a selected non-active session."),
    CommandSpec("/tree", ("/t",), "/tree", "Show the current session tree and active path."),
    CommandSpec("/checkout", ("/co",), "/checkout <message-id>", "Switch to a message branch in the active session."),
    CommandSpec("/rollback", ("/rb",), "/rollback [message-id]", "Move the active branch to a previous boundary."),
    CommandSpec("/show_context", ("/context", "/ctx"), "/show_context [--raw]", "Show the latest provider context snapshot."),
    CommandSpec("/compact", ("/cmp",), "/compact", "Compact older messages in the active session."),
    CommandSpec("/abort", ("/stop",), "/abort", "Abort the active agent task."),
    CommandSpec("/permission_mode", ("/perm",), "/permission_mode [default|accept_edits|bypass_permissions]", "Show or change the permission mode."),
    CommandSpec("/exit", ("/quit", "/q"), "/exit", "Exit the interactive agent."),
)

_COMMAND_INDEX = {name: spec for spec in COMMANDS for name in spec.names}


def resolve_command(value: str) -> CommandSpec | None:
    """Resolve a slash command or bare help name to its canonical spec."""
    key = value.strip().lower()
    if not key.startswith("/"):
        key = f"/{key}"
    return _COMMAND_INDEX.get(key)


def format_help(command: str | None = None) -> str:
    """Render stable command help without depending on a specific UI."""
    if command is not None:
        spec = resolve_command(command)
        if spec is None:
            return f"Unknown command: {command}"
        aliases = ", ".join(spec.aliases) if spec.aliases else "none"
        return f"{spec.usage}\n  {spec.description}\n  aliases: {aliases}"

    lines = ["Available commands:"]
    for spec in COMMANDS:
        aliases = f" (aliases: {', '.join(spec.aliases)})" if spec.aliases else ""
        lines.append(f"  {spec.usage:<42} {spec.description}{aliases}")
    lines.append("Permission modes: default, accept_edits, bypass_permissions")
    lines.append("/resume without an id opens a numbered session selector; ids accept a full id, filename stem, or unique prefix.")
    return "\n".join(lines)


def is_exit_command(command: str) -> bool:
    parts = command.strip().split()
    spec = resolve_command(parts[0]) if parts else None
    return len(parts) == 1 and spec is not None and spec.name == "/exit"


def handle_repl_command(command: str, harness: "Harness") -> bool:
    """Handle a registered command without entering the model loop."""
    parts = command.strip().split()
    if not parts:
        return False
    spec = resolve_command(parts[0])
    if spec is None:
        return False
    if spec.name == "/exit":
        if len(parts) != 1:
            print("usage: /exit")
            return True
        return False
    name = spec.name

    if name == "/abort":
        if len(parts) != 1:
            print("usage: /abort")
        elif not harness.is_running:
            print("[abort] no active task")
        else:
            harness.abort()
            print("[abort] requested")
        return True

    if name == "/help":
        if len(parts) > 2:
            print("usage: /help [command]")
        else:
            print(format_help(parts[1] if len(parts) == 2 else None))
        return True

    if name == "/new":
        if len(parts) != 1:
            print("usage: /new")
            return True
        try:
            path = harness.new_session()
            print(f"[new] session: {harness.session_id} ({path.name})")
        except SessionError as exc:
            print(f"[new error] {exc}")
        return True

    if name == "/name":
        raw_name = command.strip().split(maxsplit=1)[1] if len(parts) > 1 else None
        if raw_name is None:
            print(f"[name] {harness.session_name or '(unnamed)'}")
            return True
        value = "" if raw_name.strip() == "-" else raw_name.strip()
        if len(value) > 120:
            print("[name error] name must be at most 120 characters")
            return True
        try:
            harness.set_session_name(value)
            print(f"[name] {harness.session_name or '(unnamed)'}")
        except SessionError as exc:
            print(f"[name error] {exc}")
        return True

    if name == "/resume":
        if len(parts) > 2:
            print("usage: /resume [session-id]")
            return True
        try:
            identifier = parts[1] if len(parts) == 2 else _choose_session(harness)
            if identifier is None:
                return True
            path = harness.resume_session(identifier)
            label = harness.session_name or "(unnamed)"
            print(f"[resume] {label} - {harness.session_id} ({path.name})")
        except SessionError as exc:
            print(f"[resume error] {exc}")
        return True

    if name == "/drop":
        if len(parts) != 2:
            print("usage: /drop <session-id>")
            return True
        identifier = parts[1]
        label = identifier
        if not _confirm_drop(label):
            print("[drop] cancelled")
            return True
        try:
            deleted = harness.drop_session(identifier)
            print(f"[drop] deleted: {deleted.name}")
        except SessionError as exc:
            print(f"[drop error] {exc}")
        return True

    if name == "/tree":
        if len(parts) != 1:
            print("usage: /tree")
            return True
        try:
            nodes = harness.session_tree()
            if not nodes:
                print("[tree] session is empty")
                return True
            print(f"[tree] {harness.session_name or '(unnamed)'}")
            print("     * active leaf, + active path")
            print(*format_tree(nodes), sep="\n")
        except SessionError as exc:
            print(f"[tree error] {exc}")
        return True

    if name == "/permission_mode":
        usage = "usage: /permission_mode [default|accept_edits|bypass_permissions]"
        if len(parts) > 2:
            print(usage)
            return True
        try:
            if len(parts) == 1:
                print(f"[permission_mode] {harness.permission_mode()}")
            else:
                mode = PERMISSION_MODE_ALIASES.get(parts[1], parts[1])
                if mode not in PERMISSION_MODES:
                    print(usage)
                    return True
                harness.set_permission_mode(mode)
                print(f"[permission_mode] switched to {mode}")
        except SessionError as exc:
            print(f"[permission_mode error] {exc}")
        return True

    if name == "/compact":
        if len(parts) > 1:
            print("usage: /compact")
            return True
        try:
            result = harness.compact()
            if result is None:
                print("[compact] context is below the configured threshold")
            else:
                print(f"[compact] summarized {result.summarized_count} messages; kept {result.kept_count}")
        except (ProviderError, SessionError) as exc:
            print(f"[compact error] {exc}")
        return True

    if name == "/show_context":
        if len(parts) > 2 or (len(parts) == 2 and parts[1] not in {"--raw", "raw"}):
            print("usage: /show_context [--raw]")
            return True
        snapshot = harness.context_snapshot()
        if snapshot is None:
            print("[show_context] no model request has been sent yet")
            return True
        print(format_context_snapshot(snapshot, redact=len(parts) == 1))
        return True

    if name == "/checkout":
        if len(parts) != 2:
            print("usage: /checkout <message-id>")
            return True
    elif name == "/rollback":
        if len(parts) > 2:
            print("usage: /rollback [message-id]")
            return True

    try:
        message_id = harness.resolve_message_id(parts[1]) if len(parts) == 2 else None
        if name == "/checkout":
            harness.checkout(message_id)
        else:
            harness.rollback(message_id)
        current = getattr(harness.session_store, "current_leaf_id", None)
        print(f"[{name[1:]}] active message: {current or 'root'}")
    except SessionError as exc:
        print(f"[{name[1:]} error] {exc}")
    return True


def format_tree(nodes, *, width: int | None = None, max_indent: int = 6) -> list[str]:
    """Render all nodes with bounded indentation and terminal-width clipping."""
    if width is None:
        width = shutil.get_terminal_size(fallback=(120, 24)).columns
    lines: list[str] = []
    for node in nodes:
        marker = "*" if node.is_leaf else "+" if node.is_active else " "
        visible_depth = min(node.depth, max_indent)
        indent = "  " * visible_depth
        if node.depth > max_indent:
            indent += "... "
        line = f"{marker} {indent}{node.message_id[:8]} [d{node.depth}] {node.role:<11} {node.preview}"
        line = _terminal_safe(line)
        if width > 1 and len(line) >= width:
            line = line[: width - 4] + "..."
        lines.append(line)
    return lines


def _terminal_safe(value: str) -> str:
    """Replace characters unsupported by the active console encoding."""
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return value
    return value.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _choose_session(harness: "Harness") -> str | None:
    """Display a textual selector and return the chosen session identifier."""
    catalog = harness.session_catalog()
    if not catalog:
        print("[resume] no persisted sessions")
        return None
    print("Available sessions:")
    for index, entry in enumerate(catalog, 1):
        name = entry["name"] or "(unnamed)"
        prompt = str(entry["first_prompt"] or "").replace("\n", " ").strip()
        if len(prompt) > 56:
            prompt = prompt[:53] + "..."
        modified = datetime.fromtimestamp(float(entry["modified"])).strftime("%Y-%m-%d %H:%M")
        print(f"  {index}. {name}  [{str(entry['id'])[:12]}]  {modified}  {prompt}")
    try:
        selected = input("select session (number or id, blank cancels)> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not selected:
        return None
    if selected.isdigit():
        index = int(selected)
        if 1 <= index <= len(catalog):
            return str(catalog[index - 1]["id"])
        print("[resume] invalid session number")
        return None
    return selected


def _confirm_drop(label: str) -> bool:
    """Require an explicit confirmation before deleting persisted history."""
    try:
        answer = input(f"Permanently delete session '{label}'? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}


__all__ = [
    "COMMANDS",
    "CommandSpec",
    "format_help",
    "format_tree",
    "handle_repl_command",
    "is_exit_command",
    "resolve_command",
]
