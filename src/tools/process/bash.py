from __future__ import annotations

from core.types import ToolSpec
from .common import CommandTool


class BashTool(CommandTool):
    tool_name = "bash"
    spec = ToolSpec(
        "bash",
        "Execute a shell command in the workspace only when a dedicated tool cannot express the task. Do not use for directory listing, file discovery, content search, or reading files; use ls, find, grep, or read instead. Output is returned with exit status.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": [],
            "additionalProperties": False,
        },
    )


__all__ = ["BashTool"]
