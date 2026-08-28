from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from core.types import ModelRequest, ProviderEvent, ToolCall


class ModelProvider(Protocol):
    def stream(self, request: ModelRequest) -> Iterable[ProviderEvent]:
        ...


class FakeProvider:
    """Deterministic provider for tests and local smoke runs."""

    def __init__(self, responses: Sequence[str | ToolCall | Sequence[ToolCall]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def stream(self, request: ModelRequest) -> Iterable[ProviderEvent]:
        if self.calls >= len(self._responses):
            yield ProviderEvent(kind="done")
            return
        response = self._responses[self.calls]
        self.calls += 1
        if isinstance(response, str):
            yield ProviderEvent(kind="text_delta", text=response)
        elif isinstance(response, ToolCall):
            yield ProviderEvent(kind="tool_call", tool_call=response)
        else:
            for call in response:
                yield ProviderEvent(kind="tool_call", tool_call=call)
        yield ProviderEvent(kind="done")
