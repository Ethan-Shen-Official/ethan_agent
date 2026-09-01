from __future__ import annotations

from typing import Any

from core.types import ToolSpec
from ..base import ToolBase, ToolContext


class WriteTool(ToolBase):
    spec = ToolSpec(
        "write",
        "Create or overwrite a UTF-8 file inside the workspace.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute workspace path."},
                "content": {"type": "string", "description": "Complete file contents."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        path = arguments["path"]
        content = arguments["content"]
        if context.cancel_event is not None and context.cancel_event.is_set():
            raise RuntimeError("operation aborted")
        context.execution_env.write_file(path, content)
        return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}"


__all__ = ["WriteTool"]
