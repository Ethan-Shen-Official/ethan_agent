from __future__ import annotations

from typing import Any

from core.types import ToolResult, ToolSpec
from .base import ToolContext


class ExecuteTool:
    spec = ToolSpec("exe", "Execute a shell command.", {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]})

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(arguments.get("_call_id", "exe"))
        try:
            code, stdout, stderr = context.execution_env.execute(arguments["cmd"])
            content = f"exit_code={code}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            return ToolResult(call_id, self.spec.name, content, code != 0)
        except Exception as exc:
            return ToolResult(call_id, self.spec.name, str(exc), True)
