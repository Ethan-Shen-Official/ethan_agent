from __future__ import annotations

from typing import Any

from core.types import ToolSpec
from ..base import ToolBase, ToolContext


class FindTool(ToolBase):
    spec = ToolSpec(
        "find",
        "Preferred read-only tool for locating workspace files and directories by glob pattern. Use this instead of bash find/dir or shell globbing.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "integer"},
                "include_hidden": {"type": "boolean"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        pattern = arguments["pattern"]
        root = str(arguments.get("path", ".")).replace("\\", "/").strip("./")
        if root:
            pattern = f"{root}/{pattern}"
        limit = int(arguments.get("limit", 1000))
        paths = context.execution_env.search(pattern, limit, bool(arguments.get("include_hidden", False)))
        paths = [path.replace("\\", "/") for path in paths]
        return "\n".join(paths) if paths else "No files found matching pattern"


__all__ = ["FindTool"]
