from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .context import ContextBuilder, DefaultContextBuilder
from .state import LoopState
from .types import AgentEvent, Message, ModelRequest, ProviderEvent, ToolCall
from providers.base import ModelProvider
from tools.executor import ToolExecutor


@dataclass(frozen=True)
class LoopConfig:
    max_turns: int = 8
    system_prompt: str = "You are a helpful coding agent. Use tools when needed."


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
                state.stop_reason = "cancelled"
                event = AgentEvent("turn_end", {"reason": state.stop_reason})
                yield event
                return
            if state.turn_count >= self.config.max_turns:
                state.stop_reason = "max_turns"
                event = AgentEvent("turn_end", {"reason": state.stop_reason})
                yield event
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
                state.stop_reason = "provider_error"
                event = AgentEvent("error", {"message": provider_error})
                yield event
                end = AgentEvent("turn_end", {"reason": state.stop_reason})
                yield end
                return
            state.total_tokens += usage
            yield AgentEvent("assistant_message", {"message": assistant})
            calls = self.finalize_assistant(assistant, state)
            if not calls:
                state.messages.append(assistant)
                state.stop_reason = "completed"
                end = AgentEvent("turn_end", {"reason": state.stop_reason, "message": assistant.content})
                yield end
                return
            state.messages.append(assistant)
            results = []
            for event in self.tool_executor.execute(calls):
                yield event
                if event.kind == "tool_result":
                    results.append(event.data["result"])
            for result in results:
                state.messages.append(Message.tool(result))
            state.turn_count += 1

    def prepare_context(self, state: LoopState) -> ModelRequest:
        return self.context_builder.build(state, self.tool_specs, self.config.system_prompt)

    def stream_model(self, request: ModelRequest):
        return self.provider.stream(request)

    def finalize_assistant(self, assistant: Message, state: LoopState) -> list[ToolCall]:
        return assistant.tool_calls
