"""Small, provider-independent context compaction primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.errors import ProviderError
from core.types import Message, ModelRequest


SUMMARY_PROMPT = """Summarize the coding-agent conversation below for a later model turn.
Use exactly these Markdown sections and be concise:
## Goal
## Constraints & Preferences
## Progress
### Done
### In Progress
### Blocked
## Key Decisions
## Next Steps
## Critical Context
Preserve concrete file names, tool actions, errors, and unresolved work. Do not invent facts.
"""


@dataclass(frozen=True)
class CompactConfig:
    enabled: bool = True
    context_window: int = 128_000
    reserve_tokens: int = 4_096
    keep_recent_tokens: int = 16_000

    def __post_init__(self) -> None:
        if self.context_window < 1 or self.reserve_tokens < 0 or self.keep_recent_tokens < 0:
            raise ValueError("compact token limits must be non-negative and context_window must be positive")
        if self.reserve_tokens >= self.context_window:
            raise ValueError("reserve_tokens must be smaller than context_window")


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    tokens_before: int
    summarized_count: int
    kept_count: int
    details: dict[str, list[str]]
    split_turn: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "tokens_before": self.tokens_before,
            "summarized_count": self.summarized_count,
            "kept_count": self.kept_count,
            "details": self.details,
            "split_turn": self.split_turn,
        }


def estimate_tokens(message: Message) -> int:
    """Use Pi's deliberately cheap chars/4 estimate for one message."""
    chars = len(message.content or "")
    for call in message.tool_calls:
        chars += len(call.id) + len(call.name) + len(str(call.arguments))
    if message.tool_result is not None:
        result = message.tool_result
        chars += len(result.name) + len(result.tool_call_id) + len(result.content)
    return math.ceil(chars / 4)


def estimate_messages_tokens(messages: Iterable[Message]) -> int:
    return sum(estimate_tokens(message) for message in messages)


@dataclass
class TokenLedger:
    """Incrementally track message estimates with O(1) range totals."""

    _prefix: list[int] = field(default_factory=lambda: [0])

    @classmethod
    def from_messages(cls, messages: Iterable[Message]) -> "TokenLedger":
        ledger = cls()
        ledger.reset(messages)
        return ledger

    def reset(self, messages: Iterable[Message]) -> None:
        prefix = [0]
        for message in messages:
            prefix.append(prefix[-1] + estimate_tokens(message))
        self._prefix = prefix

    def append(self, message: Message) -> None:
        self._prefix.append(self._prefix[-1] + estimate_tokens(message))

    @property
    def message_count(self) -> int:
        return len(self._prefix) - 1

    @property
    def total_tokens(self) -> int:
        return self._prefix[-1]

    def range_tokens(self, start: int = 0, end: int | None = None) -> int:
        end = self.message_count if end is None else end
        if start < 0 or end < start or end > self.message_count:
            raise IndexError("token range is outside the ledger")
        return self._prefix[end] - self._prefix[start]


def should_compact(context_tokens: int, settings: CompactConfig) -> bool:
    return settings.enabled and context_tokens > settings.context_window - settings.reserve_tokens


def find_valid_cut_points(messages: list[Message]) -> list[int]:
    """Return retained-region starts that do not begin with a tool result."""
    return [index for index, message in enumerate(messages) if index > 0 and message.role in {"user", "assistant"}]


def find_cut_point(messages: list[Message], keep_recent_tokens: int) -> int | None:
    """Find a valid retained-region start in one reverse scan.

    The scan preserves the previous behavior: tool results are never a cut
    point, and if the budget is smaller than the whole list the oldest valid
    point is used as a conservative fallback.
    """
    accumulated = 0
    first_valid: int | None = None
    rightmost_valid: int | None = None
    selected: int | None = None
    threshold_index: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        is_valid = index > 0 and messages[index].role in {"user", "assistant"}
        if is_valid:
            if rightmost_valid is None:
                rightmost_valid = index
            first_valid = index
        accumulated += estimate_tokens(messages[index])
        if threshold_index is None and accumulated >= keep_recent_tokens:
            threshold_index = index
            selected = first_valid
        elif threshold_index is not None and index < threshold_index:
            if selected is not None:
                return selected
            if rightmost_valid is not None:
                return rightmost_valid
    if threshold_index is None:
        return first_valid
    return selected if selected is not None else rightmost_valid


def file_operations(messages: Iterable[Message]) -> dict[str, list[str]]:
    read: set[str] = set()
    modified: set[str] = set()
    for message in messages:
        for call in message.tool_calls:
            path = call.arguments.get("path")
            if not isinstance(path, str):
                continue
            if call.name in {"read_file", "search", "list_dir"}:
                read.add(path)
            elif call.name in {"write", "edit"}:
                modified.add(path)
    return {"read_files": sorted(read), "modified_files": sorted(modified)}


def serialize_messages(messages: Iterable[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        lines.append(f"[{message.role}] {message.content}")
        for call in message.tool_calls:
            lines.append(f"[tool call] {call.name}({call.arguments!r})")
        if message.tool_result is not None:
            lines.append(f"[tool result:{message.tool_result.name}] {message.tool_result.content}")
    return "\n".join(lines)


def summary_message(summary: str) -> Message:
    return Message.user(
        "The conversation history before this point was compacted into the following summary:\n"
        f"<summary>\n{summary}\n</summary>"
    )


class CompactionContextBuilder:
    """Decorate any ContextBuilder with a persisted summary projection."""

    def __init__(self, base) -> None:
        self.base = base
        self.summary: str | None = None
        self.summarized_count = 0

    def set_summary(self, summary: str | None, summarized_count: int = 0) -> None:
        self.summary = summary or None
        self.summarized_count = max(0, summarized_count)

    def build(self, state, tools, system_prompt) -> ModelRequest:
        request = self.base.build(state, tools, system_prompt)
        return ModelRequest(self.project_messages(request.messages), request.tools, request.system_prompt)

    def project_messages(self, messages: tuple[Message, ...] | list[Message]) -> tuple[Message, ...]:
        if not self.summary or self.summarized_count <= 0:
            return tuple(messages)
        return (summary_message(self.summary), *tuple(messages)[self.summarized_count :])


def summarize_with_provider(provider, messages: list[Message], previous_summary: str | None = None) -> str:
    history = serialize_messages(messages)
    prompt = SUMMARY_PROMPT
    if previous_summary:
        prompt += f"\nPrevious summary to update:\n{previous_summary}\n"
    prompt += f"\nConversation to summarize:\n{history}"
    text: list[str] = []
    for event in provider.stream(ModelRequest((Message.user(prompt),), (), "You summarize agent history.")):
        if event.kind == "text_delta":
            text.append(event.text)
        elif event.kind == "error":
            raise ProviderError(event.error or "compaction provider error")
    summary = "".join(text).strip()
    if not summary:
        raise ProviderError("compaction provider returned an empty summary")
    return summary


__all__ = [
    "CompactionContextBuilder",
    "CompactConfig",
    "CompactionResult",
    "TokenLedger",
    "estimate_messages_tokens",
    "estimate_tokens",
    "file_operations",
    "find_cut_point",
    "find_valid_cut_points",
    "serialize_messages",
    "should_compact",
    "summarize_with_provider",
]
