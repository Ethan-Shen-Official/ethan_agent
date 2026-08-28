from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from .state import LoopState
from .types import ModelRequest, ToolSpec


class ContextBuilder(Protocol):
    """Extension point for project instructions and context compaction."""

    def build(self, state: LoopState, tools: tuple[ToolSpec, ...], system_prompt: str) -> ModelRequest:
        ...


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_root: str
    is_git_repository: bool
    platform_name: str
    os_version: str
    shell: str
    model: str
    entrypoint: str
    current_date: str
    project_instructions: tuple[tuple[str, str], ...] = ()

    @classmethod
    def discover(
        cls,
        workspace_root: str | os.PathLike[str],
        *,
        model: str = "unknown",
        entrypoint: str = "cli",
        max_instruction_chars: int = 12_000,
    ) -> "WorkspaceContext":
        root = Path(workspace_root).resolve()
        git_repository = any((candidate / ".git").exists() for candidate in (root, *root.parents))
        instructions: list[tuple[str, str]] = []
        path = root / "AGENTS.md"
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")[:max_instruction_chars].strip()
            except OSError:
                content = ""
            if content:
                instructions.append(("AGENTS.md", content))
        return cls(
            workspace_root=str(root),
            is_git_repository=git_repository,
            platform_name=platform.system() or "unknown",
            os_version=platform.platform() or "unknown",
            shell=os.environ.get("COMSPEC") if os.name == "nt" else os.environ.get("SHELL", "unknown"),
            model=model,
            entrypoint=entrypoint,
            current_date=date.today().isoformat(),
            project_instructions=tuple(instructions),
        )


class DefaultContextBuilder:
    def __init__(
        self,
        workspace_root: str | None = None,
        system_prompt: str | None = None,
        *,
        model_name: str = "unknown",
        entrypoint: str = "cli",
        workspace_context: WorkspaceContext | None = None,
    ) -> None:
        self.workspace_context = workspace_context or (
            WorkspaceContext.discover(
                workspace_root,
                model=model_name,
                entrypoint=entrypoint,
            )
            if workspace_root
            else None
        )
        self.system_prompt = system_prompt

    def build(self, state: LoopState, tools: tuple[ToolSpec, ...], system_prompt: str) -> ModelRequest:
        prompt = self.system_prompt or system_prompt
        if self.workspace_context:
            context = self.workspace_context
            tool_names = ", ".join(spec.name for spec in tools) or "none"
            lines = [
                "Runtime context:",
                "- agent: coding-agent",
                f"- entrypoint: {context.entrypoint}",
                f"- model: {context.model}",
                f"- current_date: {context.current_date}",
                f"- workspace_root: {context.workspace_root}",
                f"- is_git_repository: {str(context.is_git_repository).lower()}",
                f"- platform: {context.platform_name}",
                f"- os_version: {context.os_version}",
                f"- shell: {context.shell or 'unknown'}",
                f"- available_tools: {tool_names}",
                "Use paths relative to workspace_root when possible. Keep all file changes inside this workspace.",
            ]
            if context.project_instructions:
                lines.append("Project instructions from AGENTS.md (follow these in addition to the system prompt):")
                for name, content in context.project_instructions:
                    lines.extend((f"--- {name} ---", content, f"--- end {name} ---"))
            prompt = f"{prompt}\n\n" + "\n".join(lines)
        return ModelRequest(tuple(state.messages), tools, prompt)
