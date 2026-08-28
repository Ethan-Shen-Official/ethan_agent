from __future__ import annotations

from typing import Any, Protocol


class PermissionManager(Protocol):
    def check(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Return allow, deny or ask. P0's default allows all operations."""
        ...


class AllowAllPermissions:
    def check(self, tool_name: str, arguments: dict[str, Any]) -> str:
        return "allow"

