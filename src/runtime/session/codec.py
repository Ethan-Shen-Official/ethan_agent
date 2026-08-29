"""Serialization for provider messages stored in a session record."""

from __future__ import annotations

from typing import Any

from core.errors import SessionError
from core.types import Message, ToolCall, ToolResult


def encode_message(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ],
        "tool_result": encode_tool_result(message.tool_result),
    }


def encode_tool_result(result: ToolResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "tool_call_id": result.tool_call_id,
        "name": result.name,
        "content": result.content,
        "is_error": result.is_error,
        "truncated": result.truncated,
        "truncated_by": result.truncated_by,
        "total_lines": result.total_lines,
        "total_bytes": result.total_bytes,
        "output_lines": result.output_lines,
        "output_bytes": result.output_bytes,
    }


def decode_message(data: Any) -> Message:
    if not isinstance(data, dict):
        raise SessionError("Session message must be an object")
    try:
        calls = [
            ToolCall(
                str(item["id"]),
                str(item["name"]),
                dict(item.get("arguments") or {}),
            )
            for item in data.get("tool_calls") or []
        ]
        raw_result = data.get("tool_result")
        result = None
        if raw_result is not None:
            if not isinstance(raw_result, dict):
                raise TypeError("tool_result must be an object")
            result = ToolResult(
                str(raw_result["tool_call_id"]),
                str(raw_result["name"]),
                str(raw_result.get("content", "")),
                bool(raw_result.get("is_error", False)),
                bool(raw_result.get("truncated", False)),
                raw_result.get("truncated_by"),
                raw_result.get("total_lines"),
                raw_result.get("total_bytes"),
                raw_result.get("output_lines"),
                raw_result.get("output_bytes"),
            )
        return Message(
            role=data["role"],
            content=str(data.get("content", "")),
            tool_calls=calls,
            tool_result=result,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionError(f"Invalid session message: {exc}") from exc
