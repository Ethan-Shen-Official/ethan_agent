"""Tool lifecycle hook implementation assembled by the harness."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from core.hooks import ToolHookContext, ToolHookDecision
from runtime.permissions import ApprovalHandler, PermissionManager, PermissionRequest


BeforeToolHook = Callable[[ToolHookContext], ToolHookDecision | None]
AfterToolHook = Callable[[ToolHookContext], ToolHookDecision | None]


@dataclass(frozen=True)
class ToolLoopHooks:
    """One preflight pipeline for custom hooks and permission policy.

    Permission is evaluated here instead of as a second executor-level gate.
    A custom hook may block or replace arguments, but a replacement is still
    evaluated by the policy before execution is allowed.
    """

    before_tool: BeforeToolHook | None = None
    after_tool: AfterToolHook | None = None
    permission_manager: PermissionManager | None = None
    approval_handler: ApprovalHandler | None = None

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
        if decision.action == "block":
            return decision

        effective_call = context.call
        if decision.action == "replace_arguments":
            effective_call = type(context.call)(
                context.call.id,
                context.call.name,
                decision.arguments,
            )
            policy_context = replace(context, call=effective_call)
        else:
            policy_context = context

        permission = self._permission_decision(policy_context)
        if permission.action == "block":
            return permission
        if decision.action == "replace_arguments":
            return decision
        return permission

    def _permission_decision(self, context: ToolHookContext) -> ToolHookDecision:
        if self.permission_manager is None:
            return ToolHookDecision()
        decision = self.permission_manager.check(
            context.call.name,
            context.call.arguments,
        )
        if decision.behavior == "allow":
            return ToolHookDecision()
        if decision.behavior == "deny":
            return ToolHookDecision(
                action="block",
                reason=decision.reason or "permission denied",
            )
        if self.approval_handler is None:
            return ToolHookDecision(
                action="block",
                reason=decision.reason or "permission requires approval",
            )
        approved = self.approval_handler(
            PermissionRequest(
                context.call.name,
                dict(context.call.arguments),
                decision.reason,
            )
        )
        if approved:
            return ToolHookDecision()
        return ToolHookDecision(action="block", reason="permission denied by user")

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
