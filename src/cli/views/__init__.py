"""Presentation projections consumed by terminal and future UIs."""

from .session import format_session_entry, is_active_entry, is_active_identifier
from .tree import TreeOverlay, build_tree_overlay

__all__ = [
    "TreeOverlay",
    "build_tree_overlay",
    "format_session_entry",
    "is_active_entry",
    "is_active_identifier",
]
