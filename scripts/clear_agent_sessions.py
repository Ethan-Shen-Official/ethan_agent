"""Remove persisted coding-agent sessions for a workspace.

Only files directly inside ``<workspace>/.agent/sessions`` are removed.  The
workspace, ``.agent`` directory, and all user files remain untouched.  This is
intended for repeatable Pi/TUI comparison runs.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _default_workspace() -> Path:
    return Path(__file__).resolve().parents[1] / "workspace"


def clear_sessions(workspace: Path, *, dry_run: bool = False) -> int:
    workspace = workspace.expanduser().resolve()
    sessions = workspace / ".agent" / "sessions"
    if not workspace.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {workspace}")
    if not sessions.exists():
        return 0
    if not sessions.is_dir():
        raise ValueError(f"session path is not a directory: {sessions}")

    removed = 0
    for entry in sessions.iterdir():
        # Session records and .head pointers are files.  Refuse nested
        # directories instead of recursively deleting an unexpected target.
        if not (entry.is_file() or entry.is_symlink()):
            raise ValueError(f"refusing to remove nested directory: {entry}")
        if not dry_run:
            entry.unlink()
        removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clear persisted agent sessions for one workspace")
    parser.add_argument("workspace", nargs="?", type=Path, default=_default_workspace())
    parser.add_argument("--dry-run", action="store_true", help="list the number of files without deleting")
    args = parser.parse_args(argv)
    try:
        count = clear_sessions(args.workspace, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    action = "would remove" if args.dry_run else "removed"
    print(f"[sessions] {action} {count} file(s) from {(args.workspace / '.agent' / 'sessions').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

