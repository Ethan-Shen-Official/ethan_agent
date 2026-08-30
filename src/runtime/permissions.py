"""Permission decisions used by the harness preflight pipeline.

Permission policy is deliberately separate from :mod:`runtime.execution`:
policy decides whether an operation may start, while the execution
environment remains the non-bypassable capability boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from typing import Any, Callable, Literal, Protocol, cast


PermissionBehavior = Literal["allow", "ask", "deny"]
PermissionMode = Literal[
    "default",
    "accept_edits",
    "bypass_permissions",
]
PERMISSION_MODES: tuple[PermissionMode, ...] = (
    "default",
    "accept_edits",
    "bypass_permissions",
)


@dataclass(frozen=True)
class PermissionDecision:
    """The result of evaluating one tool call before execution."""

    behavior: PermissionBehavior
    reason: str = ""
    rule: str | None = None

    def __post_init__(self) -> None:
        if self.behavior not in {"allow", "ask", "deny"}:
            raise ValueError(f"invalid permission behavior: {self.behavior}")


@dataclass(frozen=True)
class PermissionRequest:
    """Safe-to-display information for an interactive approval handler."""

    tool_name: str
    arguments: dict[str, Any]
    reason: str


ApprovalHandler = Callable[[PermissionRequest], bool]


class PermissionManager(Protocol):
    def check(self, tool_name: str, arguments: dict[str, Any]) -> PermissionDecision:
        """Return allow, ask or deny for a validated tool call."""
        ...


class AllowAllPermissions:
    """Compatibility policy for trusted embedding and low-level tests."""

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PermissionDecision:
        return PermissionDecision("allow", reason="allow-all policy")


class WorkspacePermissionPolicy:
    """Small default policy suitable for a local workspace agent.

    Reads are allowed. Mutations and shell commands require approval in the
    default mode. Protected metadata directories are denied before mode rules
    are considered, so a bypass mode cannot turn them into writable targets.
    """

    READ_ONLY_TOOLS = frozenset({"read_file", "list_dir", "search"})
    EDIT_TOOLS = frozenset({"write", "edit"})
    SHELL_TOOLS = frozenset({"exe"})
    _PROTECTED_SHELL_PATH = re.compile(
        r"(?i)(?<![A-Za-z0-9_.-])(?:\.agent|\.git)(?![A-Za-z0-9_.-])"
    )
    _ROOT_TARGETS = frozenset({".", "./", "*", "*.*", "./*", "/*", "/"})
    _RECURSIVE_DELETE_TOOLS = frozenset({"rmdir", "rd", "del", "erase", "remove-item", "ri"})

    def __init__(
        self,
        mode: PermissionMode = "default",
        *,
        allowed_tools: set[str] | frozenset[str] = frozenset(),
        denied_tools: set[str] | frozenset[str] = frozenset(),
        protected_path_parts: tuple[str, ...] = (".agent", ".git"),
    ) -> None:
        self.mode = self._validate_mode(mode)
        self.allowed_tools = frozenset(allowed_tools)
        self.denied_tools = frozenset(denied_tools)
        self.protected_path_parts = frozenset(
            part.replace("\\", "/").strip("/").lower()
            for part in protected_path_parts
            if part
        )

    @staticmethod
    def _validate_mode(mode: str) -> PermissionMode:
        if mode not in PERMISSION_MODES:
            raise ValueError(f"invalid permission mode: {mode}")
        return cast(PermissionMode, mode)

    def set_mode(self, mode: PermissionMode) -> None:
        """Change the policy for subsequent tool calls."""
        self.mode = self._validate_mode(mode)

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PermissionDecision:
        if tool_name in self.denied_tools:
            return PermissionDecision("deny", "tool is denied by policy", f"deny:{tool_name}")

        protected_rule = self._protected_operation_rule(tool_name, arguments)
        if protected_rule is not None:
            reason = (
                "destructive workspace-wide command is blocked"
                if protected_rule == "destructive-command"
                else "protected workspace metadata cannot be modified by tools"
            )
            return PermissionDecision(
                "deny",
                reason,
                protected_rule,
            )

        if tool_name in self.allowed_tools:
            return PermissionDecision("allow", "tool is allowed by policy", f"allow:{tool_name}")

        if self.mode == "bypass_permissions":
            return PermissionDecision("allow", "bypass_permissions mode", "mode:bypass_permissions")

        if tool_name in self.READ_ONLY_TOOLS:
            return PermissionDecision("allow", "read-only tool", "read-only")

        if tool_name in self.EDIT_TOOLS:
            if self.mode == "accept_edits":
                return PermissionDecision("allow", "accept_edits mode", "mode:accept_edits")
            return PermissionDecision("ask", "editing files requires approval", f"edit:{tool_name}")

        if tool_name in self.SHELL_TOOLS:
            return PermissionDecision("ask", "shell commands require approval", "shell")

        return PermissionDecision("ask", "tool is not in the workspace allowlist", f"unknown:{tool_name}")

    def _protected_operation_rule(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        if tool_name == "exe":
            command = arguments.get("cmd")
            if not isinstance(command, str):
                return None
            if self._is_destructive_shell_command(command):
                return "destructive-command"
            if self._PROTECTED_SHELL_PATH.search(command):
                return "protected-path"
            return None
        if tool_name not in self.EDIT_TOOLS:
            return None
        path = arguments.get("path")
        if not isinstance(path, str):
            return None
        parts = {
            part.lower()
            for part in path.replace("\\", "/").split("/")
            if part not in {"", ".", ".."}
        }
        return "protected-path" if parts & self.protected_path_parts else None

    @classmethod
    def _is_destructive_shell_command(cls, command: str) -> bool:
        """Detect common workspace-wide deletion commands before shell launch.

        This is intentionally conservative. It covers direct commands and
        simple command chains; execution isolation remains the stronger future
        boundary for obfuscated or script-generated commands.
        """
        for segment in re.split(r"[;&|\n]+", command):
            if not segment.strip():
                continue
            try:
                tokens = shlex.split(segment, posix=False)
            except ValueError:
                tokens = segment.split()
            tokens = [token.strip("\"'") for token in tokens]
            if not tokens:
                continue
            name = tokens[0].lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            if name in {"rm", "remove-item", "ri"} and cls._is_recursive_force_root_delete(tokens[1:]):
                return True
            if name in cls._RECURSIVE_DELETE_TOOLS and cls._is_cmd_recursive_root_delete(name, tokens[1:]):
                return True
            if name == "find" and cls._is_find_delete(tokens[1:]):
                return True
            if name == "git" and cls._is_git_clean(tokens[1:]):
                return True
        return False

    @classmethod
    def _is_recursive_force_root_delete(cls, tokens: list[str]) -> bool:
        recursive = force = False
        targets: list[str] = []
        options = True
        for token in tokens:
            lower = token.lower()
            if options and lower == "--":
                options = False
                continue
            if options and lower.startswith("-"):
                compact = lower.lstrip("-")
                recursive |= compact in {"r", "rf", "fr"} or "r" in compact or lower == "--recursive"
                force |= compact in {"f", "rf", "fr"} or "f" in compact or lower == "--force"
                continue
            targets.append(token)
        return recursive and force and any(cls._is_root_target(target) for target in targets)

    @classmethod
    def _is_cmd_recursive_root_delete(cls, name: str, tokens: list[str]) -> bool:
        recursive = any(token.lower() == "/s" or token.lower() == "-recurse" for token in tokens)
        targets = [token for token in tokens if not token.startswith(("/", "-"))]
        if name in {"del", "erase"}:
            return recursive and any(cls._is_root_target(target) or "*" in target for target in targets)
        return recursive and any(cls._is_root_target(target) for target in targets)

    @classmethod
    def _is_find_delete(cls, tokens: list[str]) -> bool:
        return "-delete" in {token.lower() for token in tokens} and any(
            cls._is_root_target(token) for token in tokens if not token.startswith("-")
        )

    @staticmethod
    def _is_git_clean(tokens: list[str]) -> bool:
        if not tokens or tokens[0].lower() != "clean":
            return False
        return any("f" in token.lower().lstrip("-") for token in tokens[1:])

    @classmethod
    def _is_root_target(cls, target: str) -> bool:
        normalized = target.strip("\"'").replace("\\", "/").lower()
        if normalized in cls._ROOT_TARGETS:
            return True
        return bool(re.fullmatch(r"[a-z]:/?", normalized))


__all__ = [
    "AllowAllPermissions",
    "ApprovalHandler",
    "PermissionBehavior",
    "PermissionDecision",
    "PermissionManager",
    "PermissionMode",
    "PERMISSION_MODES",
    "PermissionRequest",
    "WorkspacePermissionPolicy",
]
