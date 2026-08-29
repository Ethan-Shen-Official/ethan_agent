from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from core.types import ToolResult, ToolSpec


@dataclass(frozen=True)
class ToolContext:
    execution_env: Any
    permission_manager: Any


class Tool(Protocol):
    """Stable runtime contract consumed by the registry and executor."""

    spec: ToolSpec

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ...


class ToolBase(ABC):
    """Common boundary for built-in tools and future local adapters.

    The executor owns lookup, schema validation and permissions. This class
    owns call-id extraction, hiding executor metadata, and error normalization.
    """

    spec: ToolSpec

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(arguments.get("_call_id", self.spec.name))
        implementation_args = {
            key: value for key, value in arguments.items() if key != "_call_id"
        }
        try:
            result = self.run(implementation_args, context)
        except Exception as exc:
            return ToolResult(call_id, self.spec.name, str(exc), True)
        if isinstance(result, ToolResult):
            return ToolResult(call_id, self.spec.name, result.content, result.is_error)
        return ToolResult(call_id, self.spec.name, str(result), False)

    @abstractmethod
    def run(self, arguments: dict[str, Any], context: ToolContext) -> str | ToolResult:
        """Implement the tool using validated arguments and injected context."""
        ...
