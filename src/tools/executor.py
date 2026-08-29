from __future__ import annotations

from time import perf_counter

from core.hooks import ToolHookContext, ToolHookDecision
from core.types import AgentEvent, ToolResult
from .base import ToolContext
from .registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext, hooks=None) -> None:
        self.registry = registry
        self.context = context
        # The executor depends only on the small core hook protocol. Concrete
        # policies remain assembled by Harness.
        self.hooks = hooks

    def execute(self, calls):
        for call in calls:
            yield AgentEvent("tool_start", {"id": call.id, "name": call.name, "arguments": call.arguments})
            tool = self.registry.get(call.name)
            if tool is None:
                result = ToolResult(call.id, call.name, f"unknown tool: {call.name}", True)
                terminate = False
            else:
                result, terminate = self._execute_single(tool, call)
            yield AgentEvent("tool_result", {"result": result, "terminate": terminate})
            yield AgentEvent("tool_progress", {"id": call.id, "complete": True})

    def _execute_single(self, tool, call):
        try:
            # One common schema check is enough for the normal path. A second
            # check only protects a call whose arguments were replaced by a
            # before hook.
            self._validate_arguments(tool.spec.input_schema, call.arguments)
            original_call = call
            call = self._before_tool(tool, call)
            if call is not original_call:
                self._validate_arguments(tool.spec.input_schema, call.arguments)
            decision = self.context.permission_manager.check(call.name, call.arguments)
            if decision != "allow":
                return ToolResult(call.id, call.name, f"permission {decision}", True), False
            started = perf_counter()
            result = tool.execute({**call.arguments, "_call_id": call.id}, self.context)
            elapsed_ms = int((perf_counter() - started) * 1000)
            return self._after_tool(tool, call, result, elapsed_ms)
        except Exception as exc:
            return ToolResult(call.id, call.name, str(exc), True), False

    def _invoke_hook(self, name: str, context: ToolHookContext) -> ToolHookDecision:
        if self.hooks is None:
            return ToolHookDecision()
        callback = getattr(self.hooks, name, None)
        if callback is None:
            return ToolHookDecision()
        decision = callback(context)
        if decision is None:
            return ToolHookDecision()
        if not isinstance(decision, ToolHookDecision):
            raise TypeError(f"{name} hook must return ToolHookDecision or None")
        return decision

    def _before_tool(self, tool, call):
        context = ToolHookContext("before_tool", call, tool, self.context)
        decision = self._invoke_hook("before", context)
        if decision.action == "allow":
            if decision.result is not None or decision.terminate:
                raise ValueError("before_tool allow cannot contain result or terminate")
            return call
        if decision.action == "block":
            raise PermissionError(decision.reason or "blocked by before_tool hook")
        if decision.action == "replace_arguments":
            if not isinstance(decision.arguments, dict):
                raise ValueError("replace_arguments requires an object")
            if decision.result is not None or decision.terminate:
                raise ValueError("replace_arguments cannot contain result or terminate")
            return type(call)(call.id, call.name, decision.arguments)
        raise ValueError(f"invalid before_tool action: {decision.action}")

    def _after_tool(self, tool, call, result, elapsed_ms):
        context = ToolHookContext("after_tool", call, tool, self.context, result, elapsed_ms)
        try:
            decision = self._invoke_hook("after", context)
            if decision.action == "allow":
                if decision.result is not None:
                    raise ValueError("allow cannot contain a replacement result")
                return result, bool(decision.terminate)
            if decision.action == "replace_result":
                replacement = decision.result
                if not isinstance(replacement, ToolResult):
                    raise TypeError("replace_result requires a ToolResult")
                if not isinstance(replacement.content, str):
                    raise TypeError("ToolResult content must be a string")
                # A hook may post-process content/error state but cannot
                # change which ToolCall this result completes.
                normalized = ToolResult(call.id, call.name, replacement.content, bool(replacement.is_error))
                return normalized, bool(decision.terminate)
            if decision.action == "stop":
                if decision.result is not None:
                    raise ValueError("stop cannot contain a replacement result")
                return result, True
            raise ValueError(f"invalid after_tool action: {decision.action}")
        except Exception as exc:
            return ToolResult(call.id, call.name, f"after_tool hook failed: {exc}", True), False

    @staticmethod
    def _validate_arguments(schema, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        if not isinstance(schema, dict):
            return
        required = schema.get("required", [])
        for name in required:
            if name not in arguments:
                raise ValueError(f"missing required argument: {name}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(arguments) - set(properties))
            if unknown:
                raise ValueError(f"unexpected argument(s): {', '.join(unknown)}")
        for name, definition in properties.items():
            if name not in arguments:
                continue
            expected = definition.get("type") if isinstance(definition, dict) else None
            value = arguments[name]
            if expected == "string" and not isinstance(value, str):
                raise ValueError(f"argument {name} must be a string")
            if expected == "object" and not isinstance(value, dict):
                raise ValueError(f"argument {name} must be an object")
            if expected == "array" and not isinstance(value, list):
                raise ValueError(f"argument {name} must be an array")
            if expected == "boolean" and not isinstance(value, bool):
                raise ValueError(f"argument {name} must be a boolean")
            if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"argument {name} must be an integer")
            if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError(f"argument {name} must be a number")
