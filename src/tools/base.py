from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from core.types import ToolResult, ToolSpec


@dataclass(frozen=True)
class ToolContext:
    execution_env: Any
    permission_manager: Any


class Tool(Protocol):
    spec: ToolSpec

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ...
