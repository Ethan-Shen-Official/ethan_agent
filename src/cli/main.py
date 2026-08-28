from __future__ import annotations

import argparse
import sys

from harness.app import Harness
from core.errors import ProviderError
from providers.openai_compatible import OpenAICompatibleProvider
from .renderer import render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lightweight coding agent")
    parser.add_argument("prompt", nargs="?", help="one prompt; omit for REPL")
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args(argv)
    try:
        provider = OpenAICompatibleProvider.from_environment()
    except ProviderError as exc:
        print(f"[provider error] {exc}", file=sys.stderr)
        return 2
    harness = Harness(provider, args.cwd)
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
        for event in harness.prompt(prompt):
            render(event)


if __name__ == "__main__":
    raise SystemExit(main())
