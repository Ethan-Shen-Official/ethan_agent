from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.errors import ProviderError
from core.types import Message, ModelRequest, ProviderEvent, ToolCall, ToolSpec


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _first_nonempty(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _endpoint(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"


def _message_payload(message: Message) -> dict[str, Any]:
    if message.role == "tool" and message.tool_result is not None:
        return {"role": "tool", "tool_call_id": message.tool_result.tool_call_id, "content": message.tool_result.content}
    payload: dict[str, Any] = {"role": message.role, "content": message.content or None}
    if message.role == "assistant" and message.tool_calls:
        payload["tool_calls"] = [{"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}} for call in message.tool_calls]
    return payload


def _tool_payload(spec: ToolSpec) -> dict[str, Any]:
    schema = spec.input_schema or {"type": "object", "properties": {}}
    return {"type": "function", "function": {"name": spec.name, "description": spec.description, "parameters": schema}}


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 120.0


class OpenAICompatibleProvider:
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    @classmethod
    def from_environment(cls, dotenv_path: str | os.PathLike[str] = ".env") -> "OpenAICompatibleProvider":
        _load_dotenv(Path(dotenv_path))
        api_key = _first_nonempty("CODING_AGENT_API_KEY", "OPENAI_API_KEY", "API_KEY")
        base_url = _first_nonempty("CODING_AGENT_BASE_URL", "OPENAI_BASE_URL", "BASE_URL")
        model = _first_nonempty("CODING_AGENT_MODEL", "DEEPSEEK_MODEL", "MODEL") or "deepseek-v4-flash"
        if not api_key:
            raise ProviderError("Missing API key. Set OPENAI_API_KEY or CODING_AGENT_API_KEY in .env.")
        if not base_url:
            raise ProviderError("Missing base URL. Set CODING_AGENT_BASE_URL in .env.")
        timeout_text = _first_nonempty("CODING_AGENT_TIMEOUT", "OPENAI_TIMEOUT")
        try:
            timeout = float(timeout_text) if timeout_text else 120.0
        except ValueError as exc:
            raise ProviderError("CODING_AGENT_TIMEOUT must be a number.") from exc
        return cls(OpenAICompatibleConfig(api_key, base_url, model, timeout))

    def stream(self, request: ModelRequest):
        payload: dict[str, Any] = {"model": self.config.model, "messages": self._messages(request), "stream": True}
        if request.tools:
            payload["tools"] = [_tool_payload(spec) for spec in request.tools]
        http_request = urllib.request.Request(_endpoint(self.config.base_url), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "coding-agent/0.1"}, method="POST")
        try:
            with urllib.request.urlopen(http_request, timeout=self.config.timeout) as response:
                yield from self._parse_sse(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ProviderError(f"Model API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Could not connect to model API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError("Model API request timed out.") from exc

    def _messages(self, request: ModelRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(_message_payload(message) for message in request.messages)
        return messages

    def _parse_sse(self, response):
        tool_chunks: dict[int, dict[str, str]] = {}
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                yield from self._finalize_tool_calls(tool_chunks)
                yield ProviderEvent(kind="done")
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ProviderError("Model API returned invalid SSE JSON.") from exc
            if chunk.get("error"):
                error = chunk["error"]
                message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                yield ProviderEvent(kind="error", error=message)
                return
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                tokens = usage.get("total_tokens") or usage.get("completion_tokens") or 0
                if isinstance(tokens, int) and tokens:
                    yield ProviderEvent(kind="usage", tokens=tokens)
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield ProviderEvent(kind="text_delta", text=content)
                for tool_chunk in delta.get("tool_calls") or []:
                    index = int(tool_chunk.get("index", 0))
                    state = tool_chunks.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    state["id"] += str(tool_chunk.get("id") or "")
                    function = tool_chunk.get("function") or {}
                    state["name"] += str(function.get("name") or "")
                    state["arguments"] += str(function.get("arguments") or "")
        yield from self._finalize_tool_calls(tool_chunks)
        yield ProviderEvent(kind="done")

    @staticmethod
    def _finalize_tool_calls(tool_chunks: dict[int, dict[str, str]]):
        for index in sorted(tool_chunks):
            state = tool_chunks[index]
            try:
                arguments = json.loads(state["arguments"] or "{}")
            except json.JSONDecodeError as exc:
                yield ProviderEvent(kind="error", error=f"Invalid arguments for tool {state['name']}: {exc}")
                continue
            if not isinstance(arguments, dict):
                yield ProviderEvent(kind="error", error=f"Arguments for tool {state['name']} must be a JSON object.")
                continue
            yield ProviderEvent(kind="tool_call", tool_call=ToolCall(state["id"] or f"call_{index}", state["name"], arguments))
