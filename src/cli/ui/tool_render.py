"""Pi-inspired rendering helpers for completed tool calls.

The agent protocol deliberately keeps tool results plain.  This module is a
presentation-only projection: it derives a small, bounded edit diff from the
original tool arguments without changing the ToolResult contract.
"""

from __future__ import annotations

import difflib


def _edit_pairs(arguments: dict) -> list[tuple[str, str]]:
    """Read the canonical edit list used by the edit tool."""
    edits = arguments.get("edits")
    if not isinstance(edits, list):
        return []

    pairs: list[tuple[str, str]] = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        old = edit.get("oldText")
        new = edit.get("newText")
        if isinstance(old, str) and isinstance(new, str):
            pairs.append((old, new))
    return pairs


def edit_diff_lines(arguments: object, *, max_lines: int = 24) -> tuple[list[str], int]:
    """Return compact line-oriented diff rows and the number omitted.

    The edit tool's canonical ``edits`` list is projected here so the core
    ToolResult contract remains text-only.
    """
    if not isinstance(arguments, dict):
        return [], 0
    edits = _edit_pairs(arguments)
    if not edits:
        return [], 0
    rows: list[str] = []
    for old, new in edits:
        old_lines = old.splitlines() or [""]
        new_lines = new.splitlines() or [""]
        old_no = new_no = 1
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    rows.append(f"  {old_no:>4} {line}")
                    old_no += 1
                    new_no += 1
            elif tag in {"delete", "replace"}:
                for line in old_lines[i1:i2]:
                    rows.append(f"- {old_no:>4} {line}")
                    old_no += 1
                if tag == "replace":
                    for line in new_lines[j1:j2]:
                        rows.append(f"+ {new_no:>4} {line}")
                        new_no += 1
            elif tag == "insert":
                for line in new_lines[j1:j2]:
                    rows.append(f"+ {new_no:>4} {line}")
                    new_no += 1
    omitted = max(0, len(rows) - max_lines)
    return rows[:max_lines], omitted


def is_diff_line(line: str) -> str | None:
    """Return the diff class for a rendered row."""
    if line.startswith("- "):
        return "removed"
    if line.startswith("+ "):
        return "added"
    if line.startswith("  "):
        return "context"
    return None


__all__ = ["edit_diff_lines", "is_diff_line"]
