from __future__ import annotations

from core.types import AgentEvent, ToolResult
from .base import ToolContext
from .registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext) -> None:
        self.registry = registry
        self.context = context

    def execute(self, calls):
        for call in calls:
            yield AgentEvent("tool_start", {"id": call.id, "name": call.name, "arguments": call.arguments})
            tool = self.registry.get(call.name)
            if tool is None:
                result = ToolResult(call.id, call.name, f"unknown tool: {call.name}", True)
            else:
                result = self._execute_one(tool, call)
            yield AgentEvent("tool_result", {"result": result})
            yield AgentEvent("tool_progress", {"id": call.id, "complete": True})

    def _execute_one(self, tool, call):
        try:
            self._validate_arguments(tool.spec.input_schema, call.arguments)
            decision = self.context.permission_manager.check(call.name, call.arguments)
            if decision != "allow":
                return ToolResult(call.id, call.name, f"permission {decision}", True)
            return tool.execute({**call.arguments, "_call_id": call.id}, self.context)
        except Exception as exc:
            return ToolResult(call.id, call.name, str(exc), True)

    @staticmethod
    def _validate_arguments(schema, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        required = schema.get("required", []) if isinstance(schema, dict) else []
        for name in required:
            if name not in arguments:
                raise ValueError(f"missing required argument: {name}")
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        for name, definition in properties.items():
            if name not in arguments:
                continue
            expected = definition.get("type") if isinstance(definition, dict) else None
            value = arguments[name]
            if expected == "string" and not isinstance(value, str):
                raise ValueError(f"argument {name} must be a string")
