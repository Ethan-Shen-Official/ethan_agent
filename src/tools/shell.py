from __future__ import annotations

from typing import Any

from core.types import ToolResult, ToolSpec
from .base import ToolBase, ToolContext


class ShellTool(ToolBase):
    spec = ToolSpec(
        "exe",
        "Execute a shell command with the workspace as its working directory.",
        {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Command to execute."},
            },
            "required": ["cmd"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        execute = context.execution_env.execute
        if context.cancel_event is None:
            code, stdout, stderr = execute(arguments["cmd"])
        else:
            try:
                code, stdout, stderr = execute(
                    arguments["cmd"], cancel_event=context.cancel_event
                )
            except TypeError as exc:
                # Keep older custom ExecutionEnv implementations usable.
                if "cancel_event" not in str(exc):
                    raise
                code, stdout, stderr = execute(arguments["cmd"])
        content = f"exit_code={code}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        if code != 0:
            return ToolResult(self.spec.name, self.spec.name, content, True)
        return content
