"""Tool metadata kept outside the stable core ToolSpec contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from core.types import ToolSpec


ToolRisk = Literal["read_only", "write", "shell", "destructive"]
ExecutionMode = Literal["sequential", "parallel"]


@dataclass(frozen=True)
class ToolDefinition:
    """Pi-style metadata for a tool without changing core protocol types."""

    spec: ToolSpec
    label: str | None = None
    risk: ToolRisk = "read_only"
    prompt_snippet: str | None = None
    prompt_guidelines: tuple[str, ...] = ()
    execution_mode: ExecutionMode = "sequential"
    prepare_arguments: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.spec.name


__all__ = ["ExecutionMode", "ToolDefinition", "ToolRisk"]
