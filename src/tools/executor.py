from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from time import perf_counter

from core.errors import ToolError, format_tool_error
from core.hooks import ToolHookContext, ToolHookDecision
from core.types import AgentEvent, ToolResult
from .base import ToolContext
from .details import ToolDetailsStore
from .registry import ToolRegistry
from .truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    truncate_head,
    truncate_tail,
    truncation_notice,
)


@dataclass(frozen=True)
class ToolOutputLimits:
    max_lines: int = DEFAULT_MAX_LINES
    max_bytes: int = DEFAULT_MAX_BYTES

    def __post_init__(self) -> None:
        if self.max_lines < 1:
            raise ValueError("max_lines must be at least 1")
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        context: ToolContext,
        hooks=None,
        output_limits: ToolOutputLimits | None = None,
    ) -> None:
        self.registry = registry
        self.context = context
        self.hooks = hooks
        self.output_limits = output_limits or ToolOutputLimits()
        self.details_store = getattr(context, "details_store", None) or ToolDetailsStore()

    def bind_cancel_event(self, cancel_event: threading.Event | None) -> None:
        """Bind the cancellation event for the one active Harness run."""
        self.context = replace(self.context, cancel_event=cancel_event)

    def tool_details(self, call_id: str):
        """Return transient rich details for a completed or active call."""
        return self.details_store.get(call_id) if self.details_store is not None else None

    def execute(self, calls):
        for call in calls:
            yield AgentEvent(
                "tool_start",
                {"id": call.id, "name": call.name, "arguments": call.arguments},
            )
            if self.context.cancel_event is not None and self.context.cancel_event.is_set():
                result = self._error_result(
                    call,
                    ToolError(
                        "tool execution cancelled",
                        code="cancelled",
                        retryable=False,
                    ),
                )
                terminate = False
                updates = []
            else:
                tool = self.registry.get(call.name)
                if tool is None:
                    result = self._error_result(
                        call,
                        ToolError(
                            f"unknown tool: {call.name}",
                            code="unknown_tool",
                            retryable=False,
                        ),
                    )
                    terminate = False
                    updates = []
                else:
                    result, terminate, updates = self._execute_single(tool, call)
                    for update in updates:
                        yield update
            yield AgentEvent("tool_result", {"result": result, "terminate": terminate})
            yield AgentEvent("tool_progress", {"id": call.id, "complete": True})

    def _execute_single(self, tool, call):
        updates: list[AgentEvent] = []
        update_callback = lambda data: updates.append(
            AgentEvent("tool_update", {"id": call.id, "name": call.name, **dict(data)})
        )
        tool_context = replace(
            self.context,
            call_id=call.id,
            on_update=update_callback,
            details_store=self.details_store,
        )
        try:
            arguments = self._prepare_arguments(tool, call.arguments)
            self._validate_arguments(tool.spec.input_schema, arguments)
            if arguments != call.arguments:
                call = type(call)(call.id, call.name, arguments)
            original_call = call
            call = self._before_tool(tool, call)
            if call is not original_call:
                self._validate_arguments(tool.spec.input_schema, call.arguments)

            started = perf_counter()
            result = tool.execute({**call.arguments, "_call_id": call.id}, tool_context)
            elapsed_ms = int((perf_counter() - started) * 1000)
            result = self._truncate_result(call, result)
            normalized, terminate = self._after_tool(tool, call, result, elapsed_ms)
            return normalized, terminate, updates
        except Exception as exc:
            return self._error_result(call, exc), False, updates

    @staticmethod
    def _prepare_arguments(tool, arguments):
        prepare = getattr(tool, "prepare_arguments", None)
        if prepare is None:
            return arguments
        prepared = prepare(dict(arguments))
        if not isinstance(prepared, dict):
            raise TypeError("prepare_arguments must return an object")
        return prepared

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
            raise ToolError(
                decision.reason or "blocked by before_tool hook",
                code="permission_denied",
                retryable=False,
            )
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
                normalized = ToolResult(
                    call.id,
                    call.name,
                    replacement.content,
                    bool(replacement.is_error),
                    replacement.truncated,
                    replacement.truncated_by,
                    replacement.total_lines,
                    replacement.total_bytes,
                    replacement.output_lines,
                    replacement.output_bytes,
                )
                return self._truncate_result(call, normalized), bool(decision.terminate)
            if decision.action == "stop":
                if decision.result is not None:
                    raise ValueError("stop cannot contain a replacement result")
                return result, True
            raise ValueError(f"invalid after_tool action: {decision.action}")
        except Exception as exc:
            return self._error_result(call, exc, phase="after_tool hook failed"), False

    def _error_result(self, call, error: BaseException, *, phase: str | None = None) -> ToolResult:
        message = format_tool_error(error)
        if phase:
            message = f"{phase}: {message}"
        return ToolResult(call.id, call.name, message, True)

    def _truncate_result(self, call, result) -> ToolResult:
        if not isinstance(result, ToolResult):
            result = ToolResult(call.id, call.name, str(result), False)
        else:
            result = ToolResult(
                call.id,
                call.name,
                result.content,
                bool(result.is_error),
                result.truncated,
                result.truncated_by,
                result.total_lines,
                result.total_bytes,
                result.output_lines,
                result.output_bytes,
            )
        if not isinstance(result.content, str):
            return self._error_result(call, TypeError("ToolResult content must be a string"))

        direction = "tail" if call.name in {"bash", "powershell"} else "head"
        if direction == "tail":
            truncation = truncate_tail(
                result.content,
                max_lines=self.output_limits.max_lines,
                max_bytes=self.output_limits.max_bytes,
            )
        else:
            truncation = truncate_head(
                result.content,
                max_lines=self.output_limits.max_lines,
                max_bytes=self.output_limits.max_bytes,
            )
        if not truncation.truncated:
            return result

        notice = truncation_notice(truncation, direction)
        content = f"{truncation.content}\n\n{notice}" if notice else truncation.content
        return ToolResult(
            call.id,
            call.name,
            content,
            bool(result.is_error),
            True,
            truncation.truncated_by,
            truncation.total_lines,
            truncation.total_bytes,
            truncation.output_lines,
            truncation.output_bytes,
        )

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
            if isinstance(definition, dict):
                if "enum" in definition and value not in definition["enum"]:
                    raise ValueError(f"argument {name} must be one of: {', '.join(map(str, definition['enum']))}")
                if isinstance(value, str) and "minLength" in definition and len(value) < definition["minLength"]:
                    raise ValueError(f"argument {name} is too short")
                if isinstance(value, (int, float)) and "minimum" in definition and value < definition["minimum"]:
                    raise ValueError(f"argument {name} is below minimum")
                if isinstance(value, (list, dict)) and "minItems" in definition and len(value) < definition["minItems"]:
                    raise ValueError(f"argument {name} has too few items")
                if expected == "array" and isinstance(definition.get("items"), dict):
                    for item in value:
                        ToolExecutor._validate_arguments(definition["items"], item)
