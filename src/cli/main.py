from __future__ import annotations

import argparse
import sys

from core.errors import ProviderError
from core.loop import DEFAULT_MAX_TURNS
from harness.app import Harness
from harness.approval import PromptApprovalHandler
from providers.openai_compatible import OpenAICompatibleProvider
from runtime.permissions import PERMISSION_MODES
from .commands import handle_repl_command, is_exit_command
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
    parser.add_argument(
        "--permission-mode",
        choices=PERMISSION_MODES,
        default="default",
        help="tool permission mode (default: default)",
    )
    return parser


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
        permission_mode=args.permission_mode,
        approval_handler=PromptApprovalHandler(),
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
        if prompt.strip() == "":
            continue
        if is_exit_command(prompt):
            return 0
        if handle_repl_command(prompt, harness):
            continue
        for event in harness.prompt(prompt):
            render(event)


if __name__ == "__main__":
    raise SystemExit(main())
