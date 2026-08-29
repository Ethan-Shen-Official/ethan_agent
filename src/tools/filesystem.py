from __future__ import annotations

from typing import Any

from core.types import ToolResult, ToolSpec
from .base import ToolBase, ToolContext


class ReadFileTool(ToolBase):
    spec = ToolSpec(
        "read_file",
        "Read a UTF-8 text file inside the workspace.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "max_chars": {
                    "type": "integer",
                    "description": "Optional output limit; use a smaller value for large files.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        content = context.execution_env.read_file(arguments["path"])
        max_chars = arguments.get("max_chars")
        if max_chars is not None:
            if max_chars < 1:
                raise ValueError("max_chars must be at least 1")
            if len(content) > max_chars:
                omitted = len(content) - max_chars
                return f"{content[:max_chars]}\n...[{omitted} characters omitted]"
        return content


class WriteFileTool(ToolBase):
    spec = ToolSpec(
        "write",
        "Create or overwrite a UTF-8 text file inside the workspace.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "content": {"type": "string", "description": "Complete file contents."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        context.execution_env.write_file(arguments["path"], arguments["content"])
        return f"Wrote {len(arguments['content'])} characters to {arguments['path']}"


class EditFileTool(ToolBase):
    spec = ToolSpec(
        "edit",
        "Replace an exact text snippet in a UTF-8 file. The default requires one unique match.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "old_text": {"type": "string", "description": "Exact text to replace."},
                "new_text": {"type": "string", "description": "Replacement text."},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every match instead of requiring a unique match.",
                },
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        count = context.execution_env.edit_file(
            arguments["path"],
            arguments["old_text"],
            arguments["new_text"],
            bool(arguments.get("replace_all", False)),
        )
        return f"Edited {arguments['path']}: {count} replacement(s)"


class ListDirTool(ToolBase):
    spec = ToolSpec(
        "list_dir",
        "List files and directories inside the workspace.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path, relative to workspace."},
                "depth": {"type": "integer", "description": "Traversal depth, starting at 1."},
                "max_entries": {"type": "integer", "description": "Maximum entries to return."},
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include dot-prefixed files and directories.",
                },
            },
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        entries = context.execution_env.list_dir(
            arguments.get("path", "."),
            int(arguments.get("depth", 1)),
            int(arguments.get("max_entries", 200)),
            bool(arguments.get("include_hidden", False)),
        )
        if not entries:
            return "(directory is empty)"
        result = "\n".join(entries)
        max_entries = int(arguments.get("max_entries", 200))
        if len(entries) >= max_entries:
            result += f"\n...[showing first {max_entries} entries]"
        return result


class SearchTool(ToolBase):
    spec = ToolSpec(
        "search",
        "Find workspace paths matching a glob pattern, such as '**/*.py' or '*.txt'.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern."},
                "max_results": {"type": "integer", "description": "Maximum paths to return."},
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include dot-prefixed paths.",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        paths = context.execution_env.search(
            arguments["pattern"],
            int(arguments.get("max_results", 200)),
            bool(arguments.get("include_hidden", False)),
        )
        return "\n".join(paths) if paths else "(no matches)"
