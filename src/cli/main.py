from __future__ import annotations

import argparse
import sys

from core.errors import ProviderError, SessionError
from core.loop import DEFAULT_MAX_TURNS
from harness.app import Harness
from harness.inspection import format_context_snapshot
from providers.openai_compatible import OpenAICompatibleProvider
from .renderer import render


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lightweight coding agent")
    parser.add_argument("prompt", nargs="?", help="one prompt; omit for REPL")
    parser.add_argument("--cwd", default=".")
    parser.add_argument(
        "--session-file",
        default=None,
        help="JSONL file used to persist conversation history",
    )
    parser.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="resume the most recently modified session in the workspace",
    )
    parser.add_argument(
        "--max-turns",
        type=positive_int,
        default=DEFAULT_MAX_TURNS,
        help=f"maximum agent turns per prompt (default: {DEFAULT_MAX_TURNS})",
    )
    return parser


def handle_repl_command(command: str, harness: Harness) -> bool:
    """Handle local session commands without entering the model loop."""
    parts = command.strip().split()
    if not parts or parts[0] not in {"/checkout", "/rollback", "/show_context", "/compact"}:
        return False
    name = parts[0]
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
            print(f"[compact error] {exc}", file=sys.stderr)
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
    if len(parts) > 2 or (name == "/checkout" and len(parts) != 2):
        usage = f"usage: {name} <message-id>" if name == "/checkout" else "usage: /rollback [message-id]"
        print(usage)
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
        print(f"[{name[1:]} error] {exc}", file=sys.stderr)
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        provider = OpenAICompatibleProvider.from_environment()
    except ProviderError as exc:
        print(f"[provider error] {exc}", file=sys.stderr)
        return 2
    harness = Harness(
        provider,
        args.cwd,
        max_turns=args.max_turns,
        session_path=args.session_file,
        resume=args.continue_session,
    )
    if args.prompt:
        for event in harness.prompt(args.prompt):
            render(event)
        return 0
    while True:
        try:
            prompt = input("agent> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if prompt.strip() in {"", "/exit", "/quit"}:
            if prompt.strip():
                return 0
            continue
        if handle_repl_command(prompt, harness):
            continue
        for event in harness.prompt(prompt):
            render(event)


if __name__ == "__main__":
    raise SystemExit(main())
