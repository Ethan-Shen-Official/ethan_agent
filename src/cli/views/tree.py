"""Compact Pi-style projection of a persisted session tree."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TreeOverlay:
    items: list[str]
    roles: list[str]
    identifiers: list[str]
    selected: int


def build_tree_overlay(nodes, *, max_preview: int = 84) -> TreeOverlay:
    """Render flat preorder nodes with bounded branch connectors.

    ``SessionTreeNode.children_ids`` gives us sibling order without exposing
    persistence details to the TUI.  Ancestor continuation bars are derived
    from that relation, producing compact rows such as ``│  ├⊟`` and
    ``└⊟`` while keeping deep branches within a predictable width.
    """
    by_id = {str(getattr(node, "message_id", "")): node for node in nodes}
    items: list[str] = []
    roles: list[str] = []
    identifiers: list[str] = []
    selected = 0

    def is_last(node) -> bool:
        parent_id = getattr(node, "parent_id", None)
        parent = by_id.get(str(parent_id))
        if parent is None:
            return True
        children = tuple(str(value) for value in getattr(parent, "children_ids", ()) or ())
        return not children or children[-1] == str(getattr(node, "message_id", ""))

    def path_to(node) -> list[object]:
        """Return the visible root-to-node path for a preorder node."""
        path: list[object] = [node]
        current = node
        while int(getattr(current, "depth", 0) or 0) > 0:
            parent = by_id.get(str(getattr(current, "parent_id", "")))
            if parent is None:
                break
            path.append(parent)
            current = parent
        return list(reversed(path))

    def child_index(parent, child) -> int:
        children = tuple(str(value) for value in getattr(parent, "children_ids", ()) or ())
        try:
            return children.index(str(getattr(child, "message_id", "")))
        except ValueError:
            return 0

    def branch_prefix(node) -> str:
        """Keep linear chains flush-left and reserve columns for real forks."""
        path = path_to(node)
        parts: list[str] = []
        # A segment is needed only when an ancestor has multiple children.
        # The segment width matches the ``├⊟ ``/``└⊟ `` connector, so all
        # descendants of one branch remain vertically aligned.
        pairs = list(zip(path, path[1:]))
        # The final pair belongs to the current node. When that node is the
        # fork child itself, its connector occupies the column instead of a
        # continuation segment.
        if has_siblings(node) and pairs:
            pairs = pairs[:-1]
        for parent, child in pairs:
            children = tuple(getattr(parent, "children_ids", ()) or ())
            if len(children) > 1:
                # Keep each branch level to two cells.  The connector itself
                # is three cells wide (``├⊟ ``), so ``│  `` keeps descendants
                # aligned without the wide four/five-space indentation that
                # made deep trees run off-screen.
                parts.append("│  " if child_index(parent, child) < len(children) - 1 else "   ")
        return "".join(parts)

    def has_siblings(node) -> bool:
        parent = by_id.get(str(getattr(node, "parent_id", "")))
        return bool(parent and len(tuple(getattr(parent, "children_ids", ()) or ())) > 1)

    for index, node in enumerate(nodes):
        depth = int(getattr(node, "depth", 0) or 0)
        role = str(getattr(node, "role", "unknown"))
        preview = " ".join(str(getattr(node, "preview", "") or "").split())
        if role == "tool":
            label = f"[{preview}]"
        elif role == "assistant" and preview.startswith("tool: "):
            label = f"[{preview[6:]}]"
        else:
            label = f"{role}: {preview}"
        if len(label) > max_preview:
            label = label[: max_preview - 3] + "..."

        # The selector cursor (``›``) shows keyboard focus; ``*`` identifies
        # the persisted active leaf so users can see the current checkout
        # even after moving the cursor to another branch.
        marker = "*" if getattr(node, "is_leaf", False) else "•"
        if depth > 0 and has_siblings(node):
            connector = "└⊟ " if is_last(node) else "├⊟ "
            prefix = branch_prefix(node) + connector
        else:
            prefix = branch_prefix(node)
        items.append(f"{prefix}{marker} {label}")
        roles.append("tool" if role == "assistant" and preview.startswith("tool: ") else role)
        identifiers.append(str(getattr(node, "message_id", "")))
        if getattr(node, "is_leaf", False):
            selected = index
    return TreeOverlay(items, roles, identifiers, selected)


__all__ = ["TreeOverlay", "build_tree_overlay"]
