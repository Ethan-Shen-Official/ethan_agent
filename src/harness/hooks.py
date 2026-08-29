"""Tool lifecycle hook implementation assembled by the harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.hooks import ToolHookContext, ToolHookDecision


BeforeToolHook = Callable[[ToolHookContext], ToolHookDecision | None]
AfterToolHook = Callable[[ToolHookContext], ToolHookDecision | None]


@dataclass(frozen=True)
class ToolLoopHooks:
    """Optional before/after callbacks with one shared typed contract."""

    before_tool: BeforeToolHook | None = None
    after_tool: AfterToolHook | None = None

    @staticmethod
    def _invoke(callback, context: ToolHookContext) -> ToolHookDecision:
        if callback is None:
            return ToolHookDecision()
        decision = callback(context)
        if decision is None:
            return ToolHookDecision()
        if not isinstance(decision, ToolHookDecision):
            raise TypeError("tool hook must return ToolHookDecision or None")
        return decision

    def before(self, context: ToolHookContext) -> ToolHookDecision:
        if context.phase != "before_tool":
            raise ValueError("before hook requires a before_tool context")
        decision = self._invoke(self.before_tool, context)
        if decision.action not in {"allow", "block", "replace_arguments"}:
            raise ValueError(f"invalid before_tool action: {decision.action}")
        if decision.action == "replace_arguments" and not isinstance(decision.arguments, dict):
            raise ValueError("replace_arguments requires an object")
        if decision.result is not None:
            raise ValueError("before_tool cannot replace a result")
        return decision

    def after(self, context: ToolHookContext) -> ToolHookDecision:
        if context.phase != "after_tool":
            raise ValueError("after hook requires an after_tool context")
        decision = self._invoke(self.after_tool, context)
        if decision.action not in {"allow", "replace_result", "stop"}:
            raise ValueError(f"invalid after_tool action: {decision.action}")
        if decision.action == "replace_result" and decision.result is None:
            raise ValueError("replace_result requires a ToolResult")
        if decision.action != "replace_result" and decision.result is not None:
            raise ValueError("result replacement requires replace_result action")
        return decision


__all__ = [
    "AfterToolHook",
    "BeforeToolHook",
    "ToolHookContext",
    "ToolHookDecision",
    "ToolLoopHooks",
]
