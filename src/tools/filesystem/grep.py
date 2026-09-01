from __future__ import annotations

import re
from typing import Any

from core.types import ToolSpec
from ..base import ToolBase, ToolContext


class GrepTool(ToolBase):
    spec = ToolSpec(
        "grep",
        "Preferred read-only tool for searching file contents with a regular expression or literal pattern. Use this instead of bash grep/rg or shell pipelines.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "ignoreCase": {"type": "boolean"},
                "literal": {"type": "boolean"},
                "context": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        pattern = arguments["pattern"]
        if arguments.get("literal"):
            pattern = re.escape(pattern)
        flags = re.IGNORECASE if arguments.get("ignoreCase") else 0
        try:
            matcher = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"invalid grep pattern: {exc}") from exc
        root = str(arguments.get("path", ".")).replace("\\", "/").strip("./")
        glob = arguments.get("glob", "**/*")
        search_pattern = f"{root}/{glob}" if root else glob
        limit = max(1, int(arguments.get("limit", 100)))
        context_lines = max(0, int(arguments.get("context", 0)))
        paths = context.execution_env.search(search_pattern, max(limit * 4, limit), False)
        output: list[str] = []
        for path in paths:
            try:
                lines = context.execution_env.read_file(path).splitlines()
            except (OSError, UnicodeError, PermissionError):
                continue
            for index, line in enumerate(lines):
                if not matcher.search(line):
                    continue
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                for current in range(start, end):
                    marker = ":" if current == index else "-"
                    output.append(f"{path.replace(chr(92), chr(47))}{marker}{current + 1}{marker} {lines[current][:400]}")
                if len(output) >= limit:
                    return "\n".join(output[:limit]) + f"\n\n[{limit} match lines limit reached]"
        return "\n".join(output) if output else "No matches found"


__all__ = ["GrepTool"]
