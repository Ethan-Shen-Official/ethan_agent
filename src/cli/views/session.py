"""Presentation helpers for session selectors.

These functions deliberately do not depend on the TUI application.  They
keep session catalog formatting and active-session safety reusable by resume,
drop, and future non-terminal interfaces.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def format_session_entry(entry: dict[str, object]) -> str:
    name = entry.get("name") or "(unnamed)"
    prompt = str(entry.get("first_prompt") or "").replace("\n", " ").strip()
    if len(prompt) > 52:
        prompt = prompt[:49] + "..."
    try:
        modified = datetime.fromtimestamp(float(entry.get("modified", 0))).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        modified = "unknown time"
    return f"{name}  [{str(entry.get('id', ''))[:12]}]  {modified}  {prompt}"


def is_active_identifier(identifier: str, active_session_id: str) -> bool:
    value = str(identifier or "").strip()
    active = str(active_session_id or "").strip()
    return bool(value and active and (value == active or active.startswith(value)))


def is_active_entry(
    entry: dict[str, object],
    active_session_id: str,
    active_path: object = None,
) -> bool:
    if is_active_identifier(str(entry.get("id", "")), active_session_id):
        return True
    entry_path = entry.get("path")
    if active_path is None or entry_path is None:
        return False
    try:
        return Path(active_path).resolve() == Path(str(entry_path)).resolve()
    except (OSError, ValueError, TypeError):
        return False


__all__ = ["format_session_entry", "is_active_entry", "is_active_identifier"]
