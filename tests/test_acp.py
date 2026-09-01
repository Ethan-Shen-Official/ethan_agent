from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from bridge.acp.protocol import DeferredResponse, RpcError
from bridge.acp.server import AcpServer
from core.types import ProviderEvent, ToolCall
from providers.base import FakeProvider


class MemoryTransport:
    def __init__(self):
        self.notifications = []
        self.responses = {}
        self.permission_result = {"outcome": {"outcome": "selected", "optionId": "reject_once"}}
        self._lock = threading.Lock()

    def notify(self, method, params=None):
        with self._lock:
            self.notifications.append((method, params))

    def respond(self, request_id, *, result=None, error=None):
        with self._lock:
            self.responses[request_id] = (result, error)

    def request(self, method, params=None, *, timeout=None):
        self.notify(method, params)
        return self.permission_result


def make_server(tmp_path: Path, responses):
    return AcpServer(
        MemoryTransport(),
        default_cwd=tmp_path,
        provider_factory=lambda: FakeProvider(responses),
    )


def test_acp_initialize_and_new_session(tmp_path):
    server = make_server(tmp_path, ["hello"])
    result = server.handle_request(1, "initialize", {"protocolVersion": 1})
    assert result["protocolVersion"] == 1
    assert result["agentCapabilities"]["loadSession"] is False
    session = server.handle_request(2, "session/new", {"cwd": str(tmp_path), "mcpServers": []})
    assert session["sessionId"] in server._sessions


def test_acp_prompt_emits_updates_and_response(tmp_path):
    transport = MemoryTransport()
    server = AcpServer(transport, provider_factory=lambda: FakeProvider(["hello"]), default_cwd=tmp_path)
    server.handle_request(1, "initialize", {"protocolVersion": 1})
    session_id = server.handle_request(2, "session/new", {"cwd": str(tmp_path)})["sessionId"]
    deferred = server.handle_request(3, "session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": "hi"}]})
    assert isinstance(deferred, DeferredResponse)
    deadline = time.time() + 3
    while 3 not in transport.responses and time.time() < deadline:
        time.sleep(0.01)
    assert transport.responses[3][0] == {"stopReason": "end_turn"}
    updates = [p["update"] for m, p in transport.notifications if m == "session/update"]
    assert updates[0]["sessionUpdate"] == "agent_message_chunk"
    assert updates[0]["content"] == {"type": "text", "text": "hello"}


def test_acp_global_run_gate_and_cancel(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingProvider:
        def stream(self, request):
            started.set()
            release.wait(3)
            yield ProviderEvent("text_delta", text="partial")
            yield ProviderEvent("done")

        def abort(self):
            release.set()

    transport = MemoryTransport()
    server = AcpServer(transport, provider_factory=BlockingProvider, default_cwd=tmp_path)
    server.handle_request(1, "initialize", {"protocolVersion": 1})
    sid = server.handle_request(2, "session/new", {"cwd": str(tmp_path)})["sessionId"]
    server.handle_request(3, "session/prompt", {"sessionId": sid, "prompt": "run"})
    assert started.wait(2)
    with pytest.raises(RpcError, match="another session"):
        server.handle_request(4, "session/prompt", {"sessionId": sid, "prompt": "second"})
    server.handle_notification("session/cancel", {"sessionId": sid})
    deadline = time.time() + 3
    while 3 not in transport.responses and time.time() < deadline:
        time.sleep(0.01)
    assert transport.responses[3][0] == {"stopReason": "cancelled"}
