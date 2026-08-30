"""Session directory and file naming policy."""

from __future__ import annotations

import json
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
    candidates = list_session_paths(cwd)
    return candidates[0] if candidates else None


def list_session_paths(cwd: str | os.PathLike[str]) -> list[Path]:
    """Return persisted JSONL sessions for one workspace, newest first."""
    directory = session_dir(cwd)
    if not directory.is_dir():
        return []
    candidates = [path for path in directory.glob("*.jsonl") if path.is_file()]
    return sorted(candidates, key=_activity_time, reverse=True)


def resolve_session_path(cwd: str | os.PathLike[str], identifier: str) -> Path:
    """Resolve a session id, filename stem, or unique id prefix to a JSONL file."""
    value = identifier.strip()
    if not value:
        raise SessionError("Session id must not be empty")

    candidates = list_session_paths(cwd)
    exact: list[Path] = []
    matches: list[Path] = []
    for path in candidates:
        stem = path.stem
        session_id = _read_session_id(path)
        identifiers = (stem, path.name, session_id) if session_id else (stem, path.name)
        if value in identifiers:
            exact.append(path)
        elif any(item.startswith(value) for item in identifiers):
            matches.append(path)

    selected = exact if exact else matches
    if not selected:
        raise SessionError(f"Unknown session: {identifier}")
    if len(selected) > 1:
        names = ", ".join(path.stem for path in selected[:4])
        suffix = "..." if len(selected) > 4 else ""
        raise SessionError(f"Ambiguous session id '{identifier}': {names}{suffix}")
    return selected[0]


def delete_session_path(
    cwd: str | os.PathLike[str], path: str | os.PathLike[str]
) -> Path:
    """Delete one managed JSONL session and its head sidecar."""
    directory = session_dir(cwd).resolve()
    target = Path(path).expanduser().resolve()
    if target.parent != directory or target.suffix.lower() != ".jsonl":
        raise SessionError("Only workspace-managed JSONL sessions can be deleted")
    if not target.is_file():
        raise SessionError(f"Session file does not exist: {target.name}")
    try:
        target.unlink()
    except OSError as exc:
        raise SessionError(f"Could not delete session {target.name}: {exc}") from exc
    for sidecar in (target.with_suffix(".head"), target.with_suffix(".head.tmp")):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            # An orphaned head is inert once its JSONL session is gone.
            pass
    return target


def _activity_time(path: Path) -> int:
    times = [path.stat().st_mtime_ns]
    head = path.with_suffix(".head")
    if head.is_file():
        times.append(head.stat().st_mtime_ns)
    return max(times)


def _read_session_id(path: Path) -> str | None:
    """Read the first persisted session id without constructing a store."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                value = record.get("session_id") if isinstance(record, dict) else None
                return value if isinstance(value, str) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None
