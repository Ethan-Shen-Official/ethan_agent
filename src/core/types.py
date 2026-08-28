from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_result: ToolResult | None = None

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role="assistant", content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, result: ToolResult) -> "Message":
        return cls(role="tool", content=result.content, tool_result=result)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    system_prompt: str = ""


@dataclass(frozen=True)
class ProviderEvent:
    kind: Literal["text_delta", "tool_call", "usage", "error", "done"]
    text: str = ""
    tool_call: ToolCall | None = None
    tokens: int = 0
    error: str = ""


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

