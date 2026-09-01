from __future__ import annotations

from core.types import ToolSpec
from .common import CommandTool


class PowerShellTool(CommandTool):
    tool_name = "powershell"
    shell_prefix = ""
    spec = ToolSpec(
        "powershell",
        "Execute a PowerShell command in the workspace only when a dedicated tool cannot express the task. Do not use for directory listing, file discovery, content search, or reading files; use ls, find, grep, or read instead.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments, context):
        # Avoid shell quoting changes for the common Windows backend.  A
        # custom ExecutionEnv can interpret the command natively.
        command = arguments["command"]
        if context.execution_env.__class__.__module__.startswith("runtime.execution"):
            arguments = dict(arguments)
            arguments["command"] = f'powershell -NoProfile -NonInteractive -Command "{command.replace(chr(34), chr(34) + chr(34))}"'
        return super().run(arguments, context)


__all__ = ["PowerShellTool"]
