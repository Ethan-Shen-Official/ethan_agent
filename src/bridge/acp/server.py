"""ACP JSON-RPC server adapter for the coding-agent Harness."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading
from typing import Any, Callable

from core.errors import ProviderError, SessionError
from core.types import AgentEvent, ToolResult
from harness.app import Harness
from providers.openai_compatible import OpenAICompatibleProvider
from runtime.permissions import PERMISSION_MODES, PermissionMode

from .protocol import ACP_PROTOCOL_VERSION, DeferredResponse, RpcError
from .transport import JsonRpcStdio


STOP_REASON = {
    "completed": "end_turn",
    "cancelled": "cancelled",
    "max_turns": "max_turn_requests",
    "budget_exhausted": "max_tokens",
    "provider_error": "end_turn",
    "recovery_exhausted": "end_turn",
    "hook_stop": "end_turn",
}


def _text_content(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        lowered = key.lower()
        if any(part in lowered for part in ("key", "token", "password", "secret", "authorization")):
            safe[key] = "[redacted]"
        elif isinstance(value, str) and len(value) > 240:
            safe[key] = value[:240] + "..."
        else:
            safe[key] = value
    return safe


def _prompt_text(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    if not isinstance(prompt, list):
        raise RpcError(-32602, "prompt must be a string or content array")
    parts: list[str] = []
    for block in prompt:
        if not isinstance(block, dict) or block.get("type") != "text":
            raise RpcError(-32602, "only text prompt content is supported")
        value = block.get("text")
        if not isinstance(value, str):
            raise RpcError(-32602, "prompt text must be a string")
        parts.append(value)
    return "".join(parts)


@dataclass
class _Session:
    harness: Harness
    cwd: Path


class AcpServer:
    """Map ACP methods to independent Harness sessions over one stdio link."""

    _run_gate = threading.Lock()

    def __init__(
        self,
        transport: JsonRpcStdio | None = None,
        *,
        provider_factory: Callable[[], Any] | None = None,
        default_cwd: str | os.PathLike[str] = ".",
        permission_mode: PermissionMode = "default",
    ) -> None:
        self.transport = transport or JsonRpcStdio()
        self.provider_factory = provider_factory or OpenAICompatibleProvider.from_environment
        self.default_cwd = Path(default_cwd).resolve()
        self.permission_mode = permission_mode
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.RLock()
        self._protocol_initialized = False
        self._active_prompt_session: str | None = None

    def serve(self) -> None:
        self.transport.serve(self.handle_request, self.handle_notification)

    def handle_request(self, request_id: str | int, method: str, params: Any) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if not self._protocol_initialized:
            raise RpcError(-32002, "initialize must be called first")
        if method == "session/new":
            return self._new_session(params)
        if method == "session/prompt":
            return self._start_prompt(request_id, params)
        if method == "session/request_permission":
            raise RpcError(-32601, "session/request_permission is a client method")
        raise RpcError(-32601, f"method not found: {method}")

    def handle_notification(self, method: str, params: Any) -> None:
        if method == "session/cancel":
            self._cancel(params)

    def _initialize(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            params = {}
        requested = params.get("protocolVersion", ACP_PROTOCOL_VERSION)
        if requested != ACP_PROTOCOL_VERSION:
            raise RpcError(-32602, f"unsupported protocol version: {requested}")
        self._protocol_initialized = True
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentInfo": {"name": "coding-agent", "version": "0.1.0"},
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": False,
                },
                "mcpCapabilities": {"http": False, "sse": False},
            },
        }

    def _new_session(self, params: Any) -> dict[str, str]:
        if not isinstance(params, dict):
            params = {}
        raw_cwd = params.get("cwd", str(self.default_cwd))
        if not isinstance(raw_cwd, str):
            raise RpcError(-32602, "cwd must be a string")
        cwd = Path(raw_cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise RpcError(-32602, f"cwd is not a directory: {cwd}")
        try:
            provider = self.provider_factory()
            harness = Harness(
                provider,
                cwd,
                permission_mode=self.permission_mode,
                approval_handler=self._approval_handler,
            )
        except (ProviderError, SessionError, ValueError, OSError) as exc:
            raise RpcError(-32000, str(exc)) from exc
        session_id = harness.session_id
        with self._sessions_lock:
            self._sessions[session_id] = _Session(harness, cwd)
        return {"sessionId": session_id}

    def _lookup(self, params: Any) -> tuple[str, _Session]:
        if not isinstance(params, dict) or not isinstance(params.get("sessionId"), str):
            raise RpcError(-32602, "sessionId is required")
        session_id = params["sessionId"]
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise RpcError(-32004, "unknown session")
        return session_id, session

    def _start_prompt(self, request_id: str | int, params: Any) -> DeferredResponse:
        session_id, session = self._lookup(params)
        text = _prompt_text(params.get("prompt") if isinstance(params, dict) else None)
        if not self._run_gate.acquire(blocking=False):
            self._send_status(
                session_id,
                "The agent is still working on the previous prompt. "
                "Cancel it before sending another prompt.",
            )
            raise RpcError(-32001, "another session is already running")
        if session.harness.is_running:
            self._run_gate.release()
            self._send_status(
                session_id,
                "This session is still working on the previous prompt. "
                "Cancel it before sending another prompt.",
            )
            raise RpcError(-32001, "session is already running")
        self._active_prompt_session = session_id
        worker = threading.Thread(
            target=self._run_prompt,
            args=(request_id, session_id, session, text),
            name="acp-prompt",
            daemon=True,
        )
        worker.start()
        return DeferredResponse(request_id)

    def _run_prompt(
        self,
        request_id: str | int,
        session_id: str,
        session: _Session,
        text: str,
    ) -> None:
        reason = "end_turn"
        try:
            for event in session.harness.prompt(text):
                self._emit_event(session_id, event)
                if event.kind == "turn_end":
                    reason = STOP_REASON.get(event.data.get("reason"), "end_turn")
            self.transport.respond(request_id, result={"stopReason": reason})
        except Exception as exc:
            print(f"ACP prompt failed: {exc}", file=sys.stderr, flush=True)
            self._send_status(session_id, f"Agent error: {exc}")
            self.transport.respond(request_id, result={"stopReason": "end_turn"})
        finally:
            self._active_prompt_session = None
            self._run_gate.release()

    def _emit_event(self, session_id: str, event: AgentEvent) -> None:
        data = event.data
        if event.kind == "text_delta":
            text = data.get("text", "")
            if text:
                self.transport.notify(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": _text_content(text),
                        },
                    },
                )
        elif event.kind == "tool_start":
            call_id = str(data.get("id", ""))
            name = str(data.get("name", "tool"))
            self.transport.notify(
                "session/update",
                {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": call_id,
                        "title": name,
                        "status": "in_progress",
                        "rawInput": data.get("arguments", {}),
                    },
                },
            )
        elif event.kind == "tool_update":
            update: dict[str, Any] = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": str(data.get("id", "")),
                "status": "in_progress",
            }
            output = data.get("text") or data.get("output") or data.get("content")
            if isinstance(output, str) and output:
                update["content"] = [_text_content(output)]
            self.transport.notify(
                "session/update",
                {
                    "sessionId": session_id,
                    "update": update,
                },
            )
        elif event.kind == "tool_result":
            result = data.get("result")
            if isinstance(result, ToolResult):
                status = "failed" if result.is_error else "completed"
                update: dict[str, Any] = {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": result.tool_call_id,
                    "status": status,
                }
                if result.content:
                    update["content"] = [_text_content(result.content)]
                self.transport.notify(
                    "session/update",
                    {"sessionId": session_id, "update": update},
                )
        elif event.kind == "error":
            message = str(data.get("message", "provider error"))
            self._send_status(session_id, message)

    def _send_status(self, session_id: str, text: str) -> None:
        """Expose bridge-side status/errors in the client's normal transcript."""
        self.transport.notify(
            "session/update",
            {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": _text_content(text),
                },
            },
        )

    def _cancel(self, params: Any) -> None:
        try:
            _, session = self._lookup(params)
        except RpcError:
            return
        session.harness.abort()

    def _approval_handler(self, request) -> bool:
        session_id = self._active_prompt_session or ""
        result = self.transport.request(
            "session/request_permission",
            {
                "sessionId": session_id,
                "toolCall": {
                    "toolCallId": request.arguments.get("_call_id", ""),
                    "title": request.tool_name,
                    "rawInput": _safe_arguments(request.arguments),
                },
                "options": [
                    {
                        "optionId": "allow_once",
                        "name": "Allow once",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "reject_once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                ],
            },
            timeout=120.0,
        )
        return (
            isinstance(result, dict)
            and isinstance(result.get("outcome"), dict)
            and result["outcome"].get("optionId") == "allow_once"
        )


def main() -> int:
    configured_mode = os.environ.get("CODING_AGENT_PERMISSION_MODE", "default").strip()
    if configured_mode not in PERMISSION_MODES:
        print(
            "Invalid CODING_AGENT_PERMISSION_MODE; expected one of: "
            + ", ".join(PERMISSION_MODES),
            file=sys.stderr,
            flush=True,
        )
        return 2
    try:
        AcpServer(permission_mode=configured_mode).serve()
    except KeyboardInterrupt:
        return 0
    return 0


__all__ = ["AcpServer", "main"]
