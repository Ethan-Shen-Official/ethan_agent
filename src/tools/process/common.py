from __future__ import annotations

from typing import Any

from core.types import ToolResult, ToolSpec
from ..base import ToolBase, ToolContext


class CommandTool(ToolBase):
    shell_prefix = ""
    tool_name = "bash"
    spec = ToolSpec("bash", "Execute a command in the workspace.", {})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        timeout = arguments.get("timeout")
        if timeout is not None and float(timeout) <= 0:
            raise ValueError("timeout must be greater than zero")
        command = f"{self.shell_prefix}{command}" if self.shell_prefix else command
        execute = context.execution_env.execute
        kwargs: dict[str, Any] = {"cancel_event": context.cancel_event}
        if context.on_update is not None:
            kwargs["on_output"] = context.on_update
        if timeout is not None:
            kwargs["timeout"] = float(timeout)
        code, stdout, stderr = execute(command, **kwargs)
        content = f"exit_code={code}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        if code != 0:
            return ToolResult(context.call_id or self.tool_name, self.tool_name, content, True)
        return ToolResult(context.call_id or self.tool_name, self.tool_name, content, False)
