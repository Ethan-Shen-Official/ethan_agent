from __future__ import annotations

import difflib
from typing import Any

from core.types import ToolResult, ToolSpec
from ..base import ToolBase, ToolContext


class EditTool(ToolBase):
    spec = ToolSpec(
        "edit",
        "Apply one or more exact, non-overlapping replacements to a UTF-8 file.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"oldText": {"type": "string"}, "newText": {"type": "string"}},
                        "required": ["oldText", "newText"],
                        "additionalProperties": False,
                    },
                },
                "replace_all": {"type": "boolean"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments["path"]
        edits = arguments.get("edits") or []
        if not edits:
            raise ValueError("edits must contain at least one replacement")
        original = context.execution_env.read_file(path)
        replacements: list[tuple[str, str]] = []
        spans: list[tuple[int, int]] = []
        replacement_count = 0
        for edit in edits:
            old = edit.get("oldText")
            new = edit.get("newText")
            if not isinstance(old, str) or not isinstance(new, str) or not old:
                raise ValueError("each edit requires non-empty oldText and string newText")
            count = original.count(old)
            replace_all = bool(arguments.get("replace_all", False))
            if count != 1 and not replace_all:
                raise ValueError(f"oldText must match exactly once in {path}; found {count}")
            start = original.index(old)
            span = (start, start + len(old))
            if any(start < end and left < span[1] for left, end in spans):
                raise ValueError("edits must not overlap")
            spans.append(span)
            replacements.append((old, new))
            replacement_count += count if replace_all else 1
        updated = original
        for old, new in sorted(replacements, key=lambda item: original.index(item[0]), reverse=True):
            updated = updated.replace(old, new, -1 if bool(arguments.get("replace_all", False)) else 1)
        if context.cancel_event is not None and context.cancel_event.is_set():
            raise RuntimeError("operation aborted")
        context.execution_env.write_file(path, updated)
        diff = "".join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True), fromfile=path, tofile=path))
        if context.details_store is not None:
            context.details_store.put(context.call_id, {"diff": diff, "patch": diff, "path": path})
        return ToolResult(context.call_id or self.spec.name, self.spec.name, f"Successfully edited {path}: {replacement_count} replacement(s)")
__all__ = ["EditTool"]
