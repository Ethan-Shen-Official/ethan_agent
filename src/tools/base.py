from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
import threading
from typing import Any, Callable, Protocol

from core.errors import format_tool_error
from core.types import ToolResult, ToolSpec


@dataclass(frozen=True)
class ToolContext:
    """Execution dependencies supplied by the harness."""

    execution_env: Any
    cancel_event: threading.Event | None = None
    call_id: str = ""
    on_update: Callable[[dict[str, Any]], None] | None = None
    details_store: Any = None


class Tool(Protocol):
    """Stable runtime contract consumed by the registry and executor."""

    spec: ToolSpec

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ...


class ToolBase(ABC):
    """Common boundary for built-in tools and future local adapters.

    The executor owns lookup and schema validation. Permission policy is
    composed into the harness before-tool pipeline. This class owns call-id
    extraction and conversion of implementation failures into a ToolResult.
    """

    spec: ToolSpec

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(arguments.get("_call_id", self.spec.name))
        implementation_args = {
            key: value for key, value in arguments.items() if key != "_call_id"
        }
        try:
            prepare = getattr(self, "prepare_arguments", None)
            if prepare is not None:
                implementation_args = prepare(dict(implementation_args))
                if not isinstance(implementation_args, dict):
                    raise TypeError("prepare_arguments must return an object")
            result = self.run(implementation_args, replace(context, call_id=call_id))
        except Exception as exc:
            return ToolResult(call_id, self.spec.name, format_tool_error(exc), True)
        if isinstance(result, ToolResult):
            return ToolResult(
                call_id,
                self.spec.name,
                result.content,
                result.is_error,
                result.truncated,
                result.truncated_by,
                result.total_lines,
                result.total_bytes,
                result.output_lines,
                result.output_bytes,
            )
        return ToolResult(call_id, self.spec.name, str(result), False)

    @abstractmethod
    def run(self, arguments: dict[str, Any], context: ToolContext) -> str | ToolResult:
        """Implement the tool using validated arguments and injected context."""
        ...
