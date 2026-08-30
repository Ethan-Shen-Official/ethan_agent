"""UI-neutral projections of persisted session trees."""

from __future__ import annotations

from .types import SessionRecord, SessionTreeNode


def _node_role(record: SessionRecord) -> str:
    if record.record_type == "compaction":
        return "compaction"
    if record.record_type == "session_info":
        return "session_info"
    return record.message.role if record.message is not None else "unknown"


def _node_preview(record: SessionRecord) -> str:
    if record.record_type == "compaction":
        summary = (record.metadata or {}).get("summary", "")
        return str(summary).splitlines()[0] if summary else "summary checkpoint"
    if record.record_type == "session_info":
        return f"name={((record.metadata or {}).get('name') or '(unnamed)')}"

    message = record.message
    if message is None:
        return ""
    preview = message.content or ""
    if message.role == "assistant" and message.tool_calls:
        tools = ", ".join(call.name for call in message.tool_calls)
        preview = f"tool: {tools}" if not preview else f"{preview} [tool: {tools}]"
    elif message.role == "tool" and message.tool_result is not None:
        preview = f"{message.tool_result.name}: {preview}"
    preview = " ".join(preview.split())
    return preview if len(preview) <= 96 else preview[:93] + "..."


def build_session_tree_view(
    records: list[SessionRecord],
    active_path: list[SessionRecord],
    leaf_id: str | None,
) -> list[SessionTreeNode]:
    """Build a stable, read-only tree view for interface consumers.

    Tree navigation remains owned by :class:`SessionTree`; this function only
    projects records into the presentation value object used by interfaces.
    """
    known_ids = {record.message_id for record in records}
    active_ids = {record.message_id for record in active_path}
    children: dict[str | None, list[SessionRecord]] = {}
    for record in records:
        parent = record.parent_id if record.parent_id in known_ids else None
        children.setdefault(parent, []).append(record)

    nodes: list[SessionTreeNode] = []

    def visit(parent_id: str | None, depth: int) -> None:
        for record in children.get(parent_id, []):
            child_ids = tuple(child.message_id for child in children.get(record.message_id, []))
            nodes.append(
                SessionTreeNode(
                    record.message_id,
                    record.parent_id,
                    record.record_type,
                    _node_role(record),
                    _node_preview(record),
                    depth,
                    child_ids,
                    record.message_id in active_ids,
                    record.message_id == leaf_id,
                )
            )
            visit(record.message_id, depth + 1)

    visit(None, 0)
    return nodes


__all__ = ["build_session_tree_view"]
