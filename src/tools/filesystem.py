from __future__ import annotations

from typing import Any

from core.types import ToolResult, ToolSpec
from .base import ToolContext


class ReadFileTool:
    spec = ToolSpec("read_file", "Read a UTF-8 text file.", {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(arguments.get("_call_id", "read_file"))
        try:
            return ToolResult(call_id, self.spec.name, context.execution_env.read_file(arguments["path"]))
        except Exception as exc:
            return ToolResult(call_id, self.spec.name, str(exc), True)


class WriteFileTool:
    spec = ToolSpec("write", "Write UTF-8 text to a file.", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]})

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(arguments.get("_call_id", "write"))
        try:
            context.execution_env.write_file(arguments["path"], arguments["content"])
            return ToolResult(call_id, self.spec.name, "ok")
        except Exception as exc:
            return ToolResult(call_id, self.spec.name, str(exc), True)


class SearchTool:
    spec = ToolSpec("search", "Find files matching a glob pattern.", {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]})

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(arguments.get("_call_id", "search"))
        try:
            return ToolResult(call_id, self.spec.name, "\n".join(context.execution_env.search(arguments["pattern"])))
        except Exception as exc:
            return ToolResult(call_id, self.spec.name, str(exc), True)
