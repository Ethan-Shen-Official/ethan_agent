"""Session directory and file naming policy."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from core.errors import SessionError


def session_dir(cwd: str | os.PathLike[str]) -> Path:
    """Return the workspace-local session directory."""
    root = str(Path(cwd).resolve())
    return Path(
        os.environ.get(
            "CODING_AGENT_SESSION_DIR",
            str(Path(root) / ".agent" / "sessions"),
        )
    ).expanduser()


def default_session_path(cwd: str | os.PathLike[str]) -> Path:
    """Return a new, compact Pi-style session path."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    directory = session_dir(cwd)
    for _ in range(10):
        random_id = secrets.token_hex(6)
        candidate = directory / f"{timestamp}_{random_id}.jsonl"
        if not candidate.exists():
            return candidate
    raise SessionError("Could not allocate a unique session path")


def latest_session_path(cwd: str | os.PathLike[str]) -> Path | None:
    """Return the most recently active persisted session for a workspace."""
    directory = session_dir(cwd)
    if not directory.is_dir():
        return None
    candidates = [path for path in directory.glob("*.jsonl") if path.is_file()]
    if not candidates:
        return None

    def activity_time(path: Path) -> int:
        times = [path.stat().st_mtime_ns]
        head = path.with_suffix(".head")
        if head.is_file():
            times.append(head.stat().st_mtime_ns)
        return max(times)

    return max(candidates, key=activity_time)
