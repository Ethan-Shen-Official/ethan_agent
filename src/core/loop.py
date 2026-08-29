from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .context import ContextBuilder, DefaultContextBuilder
from .state import LoopState
from .types import AgentEvent, Message, ModelRequest, ProviderEvent, StopReason, ToolCall
from providers.base import ModelProvider
from tools.executor import ToolExecutor

DEFAULT_MAX_TURNS = 24


@dataclass(frozen=True)
class LoopConfig:
    max_turns: int = DEFAULT_MAX_TURNS
    system_prompt: str = "You are a helpful coding agent. Use tools when needed."

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")


class AgentLoop:
    def __init__(self, provider: ModelProvider, tool_executor: ToolExecutor, tool_specs=(), config: LoopConfig | None = None, context_builder: ContextBuilder | None = None) -> None:
        self.provider = provider
        self.tool_executor = tool_executor
        self.tool_specs = tuple(tool_specs)
        self.config = config or LoopConfig()
        self.context_builder = context_builder or DefaultContextBuilder()

    def run(self, prompt: str, state: LoopState | None = None) -> Iterator[AgentEvent]:
        state = state or LoopState()
        state.begin_run()
        state.messages.append(Message.user(prompt))
        while True:
            if state.cancelled or state.cancel_event.is_set():
                yield self._finish(state, "cancelled")
                return
            if state.turn_count >= self.config.max_turns:
                yield self._finish(state, "max_turns")
                return
            request = self.prepare_context(state)
            yield AgentEvent("request_start", {"turn": state.turn_count + 1})
            text_parts: list[str] = []
            calls: list[ToolCall] = []
            usage = 0
            provider_error: str | None = None
            try:
                for provider_event in self.stream_model(request):
                    if provider_event.kind == "text_delta":
                        text_parts.append(provider_event.text)
                        yield AgentEvent("text_delta", {"text": provider_event.text})
                    elif provider_event.kind == "tool_call" and provider_event.tool_call is not None:
                        calls.append(provider_event.tool_call)
                    elif provider_event.kind == "usage":
                        usage += provider_event.tokens
                    elif provider_event.kind == "error":
                        provider_error = provider_event.error or "provider error"
                        break
            except Exception as exc:
                provider_error = str(exc)
            assistant = Message.assistant("".join(text_parts), calls)
            if provider_error:
                yield AgentEvent("error", {"message": provider_error})
                yield self._finish(state, "provider_error")
                return
            state.total_tokens += usage
            yield AgentEvent("assistant_message", {"message": assistant})
            calls = self.finalize_assistant(assistant, state)
            if not calls:
                state.messages.append(assistant)
                yield self._finish(state, "completed", message=assistant.content)
                return
            state.messages.append(assistant)
            results = []
            terminate_after_tools = False
            for event in self.tool_executor.execute(calls):
                yield event
                if event.kind == "tool_result":
                    results.append(event.data["result"])
                    terminate_after_tools = terminate_after_tools or bool(event.data.get("terminate", False))
            for result in results:
                state.messages.append(Message.tool(result))
            if terminate_after_tools:
                yield self._finish(state, "hook_stop")
                return
            state.turn_count += 1

    @staticmethod
    def _finish(state: LoopState, reason: StopReason, **data) -> AgentEvent:
        """Set the canonical stop reason and emit the single terminal event."""
        state.stop_reason = reason
        return AgentEvent("turn_end", {"reason": reason, **data})

    def prepare_context(self, state: LoopState) -> ModelRequest:
        return self.context_builder.build(state, self.tool_specs, self.config.system_prompt)

    def stream_model(self, request: ModelRequest):
        return self.provider.stream(request)

    def finalize_assistant(self, assistant: Message, state: LoopState) -> list[ToolCall]:
        return assistant.tool_calls
