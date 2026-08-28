import json

from core.types import Message, ModelRequest, ProviderEvent, ToolCall, ToolSpec
from providers.openai_compatible import OpenAICompatibleConfig, OpenAICompatibleProvider, _endpoint


def test_endpoint_normalization():
    assert _endpoint("https://example.test/v1") == "https://example.test/v1/chat/completions"
    assert _endpoint("https://example.test/v1/chat/completions") == "https://example.test/v1/chat/completions"


def test_sse_text_and_tool_call_chunks_are_normalized():
    provider = OpenAICompatibleProvider(OpenAICompatibleConfig("key", "https://example.test/v1", "model"))
    lines = [
        b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":"{\\"path\\":"}}]}}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"a.txt\\"}"}}]}}]}\n',
        b'data: {"usage":{"total_tokens":12},"choices":[]}\n',
        b'data: [DONE]\n',
    ]
    events = list(provider._parse_sse(lines))
    assert [event.kind for event in events] == ["text_delta", "usage", "tool_call", "done"]
    assert events[0].text == "hello"
    assert events[1].tokens == 12
    assert events[2].tool_call == ToolCall("call_1", "read_file", {"path": "a.txt"})


def test_request_payload_contains_messages_and_tools():
    provider = OpenAICompatibleProvider(OpenAICompatibleConfig("key", "https://example.test/v1", "model"))
    request = ModelRequest(
        messages=(Message.user("inspect"),),
        tools=(ToolSpec("read_file", "Read", {"type": "object", "properties": {"path": {"type": "string"}}}),),
        system_prompt="system",
    )
    messages = provider._messages(request)
    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[1] == {"role": "user", "content": "inspect"}

