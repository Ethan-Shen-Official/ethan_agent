"""Pi-inspired rendering helpers for completed tool calls.

The agent protocol deliberately keeps tool results plain.  This module is a
presentation-only projection: it derives a small, bounded edit diff from the
original tool arguments without changing the ToolResult contract.
"""

from __future__ import annotations

import difflib


def edit_diff_lines(arguments: object, *, max_lines: int = 24) -> tuple[list[str], int]:
    """Return compact line-oriented diff rows and the number omitted.

    ``old_text``/``new_text`` are the same exact snippets accepted by the edit
    tool, so deriving the preview here is lossless for display and harmless
    when a provider emits a different result format.
    """
    if not isinstance(arguments, dict):
        return [], 0
    old = arguments.get("old_text")
    new = arguments.get("new_text")
    if not isinstance(old, str) or not isinstance(new, str):
        return [], 0
    old_lines = old.splitlines() or [""]
    new_lines = new.splitlines() or [""]
    rows: list[str] = []
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
