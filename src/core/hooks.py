"""Stable data contracts shared by the tool executor and harness hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .types import ToolCall, ToolResult


HookPhase = Literal["before_tool", "after_tool"]
HookAction = Literal["allow", "block", "replace_arguments", "replace_result", "stop"]


@dataclass(frozen=True)
class ToolHookContext:
    """Context passed to one tool lifecycle hook.

    before_tool receives a schema-validated call and no result.
    after_tool receives the tool's already-normalized result.
    """

    phase: HookPhase
    call: ToolCall
    tool: Any
    tool_context: Any
    result: ToolResult | None = None
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if self.phase == "before_tool" and self.result is not None:
            raise ValueError("before_tool context cannot contain a result")
        if self.phase == "after_tool" and self.result is None:
            raise ValueError("after_tool context requires a result")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms cannot be negative")


@dataclass(frozen=True)
class ToolHookDecision:
    """A phase-aware decision returned by a tool hook."""

    action: HookAction = "allow"
    arguments: dict[str, Any] | None = None
    result: ToolResult | None = None
    terminate: bool = False
    reason: str = ""


class ToolHooks(Protocol):
    """Minimal protocol consumed by ToolExecutor."""

    def before(self, context: ToolHookContext) -> ToolHookDecision | None:
        ...

    def after(self, context: ToolHookContext) -> ToolHookDecision | None:
        ...


__all__ = ["ToolHookContext", "ToolHookDecision", "ToolHooks"]
