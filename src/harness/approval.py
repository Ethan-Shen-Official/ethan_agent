"""Interactive approval adapter owned by the harness/UI boundary."""

from __future__ import annotations

import json
from typing import Any, Callable

from runtime.permissions import PermissionRequest


class PromptApprovalHandler:
    """Prompt once for an operation that the permission policy marked ``ask``."""

    def __init__(self, input_fn: Callable[[str], str] | None = None) -> None:
        self._input = input_fn or input

    def __call__(self, request: PermissionRequest) -> bool:
        details = _display_arguments(request.arguments)
        print(f"\nPermission requested: {request.tool_name} ({request.reason})")
        if details:
            print(details)
        try:
            answer = self._input("Allow this operation? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer.strip().lower() in {"y", "yes"}


def _display_arguments(arguments: dict[str, Any]) -> str:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        lowered = key.lower()
        if any(secret in lowered for secret in ("key", "token", "password", "secret", "authorization")):
            safe[key] = "[redacted]"
        elif isinstance(value, str) and len(value) > 240:
            safe[key] = value[:240] + "..."
        else:
            safe[key] = value
    return json.dumps(safe, ensure_ascii=False)


__all__ = ["PromptApprovalHandler"]
