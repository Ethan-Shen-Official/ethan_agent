from __future__ import annotations

from typing import Any

from core.types import ToolSpec
from ..base import ToolBase, ToolContext


class LsTool(ToolBase):
    spec = ToolSpec(
        "ls",
        "Preferred read-only tool for inspecting workspace directories. List directory entries, including directories with a trailing separator. Use this instead of bash/dir for directory listings.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
                "depth": {"type": "integer"},
                "include_hidden": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        limit = int(arguments.get("limit", 500))
        depth = int(arguments.get("depth", 1))
        include_hidden = bool(arguments.get("include_hidden", True))
        if limit < 1 or depth < 1:
            raise ValueError("limit and depth must be at least 1")
        entries = context.execution_env.list_dir(arguments.get("path", "."), depth, limit, include_hidden)
        return "\n".join(entries) if entries else "(empty directory)"


__all__ = ["LsTool"]
