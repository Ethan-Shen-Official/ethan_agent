from __future__ import annotations

from typing import Any

from core.types import ToolSpec
from ..base import ToolBase, ToolContext


class ReadTool(ToolBase):
    spec = ToolSpec(
        "read",
        "Preferred read-only tool for reading known UTF-8 text files inside the workspace by line range. Use this instead of bash cat/type.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute workspace path."},
                "offset": {"type": "integer", "description": "1-based first line (default 1)."},
                "limit": {"type": "integer", "description": "Maximum number of lines."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        path = arguments["path"]
        content = context.execution_env.read_file(path)
        offset = int(arguments.get("offset", 1))
        limit = arguments.get("limit")
        if offset < 1:
            raise ValueError("offset must be at least 1")
        if limit is not None and int(limit) < 1:
            raise ValueError("limit must be at least 1")

        lines = content.splitlines()
        start = offset - 1
        selected = lines[start : start + int(limit)] if limit is not None else lines[start:]
        output = "\n".join(selected)
        if content.endswith("\n") and selected:
            output += "\n"
        end = start + len(selected)
        if end < len(lines):
            output += (
                f"\n[Showing lines {offset}-{end} of {len(lines)}. "
                f"Use offset={end + 1} to continue.]"
            )
        return output


__all__ = ["ReadTool"]
