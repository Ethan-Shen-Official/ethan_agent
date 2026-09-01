"""Newline-delimited JSON-RPC transport for ACP over stdin/stdout."""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import sys
import threading
from typing import Any, Callable, TextIO

from .protocol import DeferredResponse, JSONRPC_VERSION, RpcError, error_payload


@dataclass
class _PendingResponse:
    event: threading.Event
    result: Any = None
    error: dict[str, Any] | None = None


class JsonRpcStdio:
    """Serialize JSON-RPC messages without protocol data on stderr."""

    def __init__(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        # ACP requires UTF-8 JSON regardless of the Windows console code page.
        # Use the underlying binary streams for the real process, while
        # retaining StringIO/TextIO support for embedding and tests.
        self.stdin = stdin if stdin is not None else getattr(sys.stdin, "buffer", sys.stdin)
        self.stdout = stdout if stdout is not None else getattr(sys.stdout, "buffer", sys.stdout)
        self._write_lock = threading.RLock()
        self._pending_lock = threading.RLock()
        self._pending: dict[str | int, _PendingResponse] = {}
        self._next_id = 0
        self._closed = threading.Event()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def send(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise RpcError(-32000, "ACP connection is closed")
        payload = dict(message)
        payload.setdefault("jsonrpc", JSONRPC_VERSION)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            payload = encoded + "\n"
            if isinstance(self.stdout, io.TextIOBase):
                self.stdout.write(payload)
            else:
                self.stdout.write(payload.encode("utf-8"))
            self.stdout.flush()

    def respond(
        self,
        request_id: str | int,
        *,
        result: Any = None,
        error: RpcError | dict[str, Any] | None = None,
    ) -> None:
        message: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": request_id}
        if error is None:
            message["result"] = result
        else:
            message["error"] = error_payload(error) if isinstance(error, RpcError) else dict(error)
        self.send(message)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        with self._pending_lock:
            self._next_id += 1
            request_id = f"server-{self._next_id}"
            pending = _PendingResponse(threading.Event())
            self._pending[request_id] = pending
        try:
            message: dict[str, Any] = {
                "jsonrpc": JSONRPC_VERSION,
                "id": request_id,
                "method": method,
            }
            if params is not None:
                message["params"] = params
            self.send(message)
            if not pending.event.wait(timeout):
                raise RpcError(-32000, f"ACP request timed out: {method}")
            if pending.error is not None:
                error = pending.error
                raise RpcError(
                    error.get("code", -32000),
                    error.get("message", "ACP request failed"),
                    error.get("data"),
                )
            return pending.result
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def serve(
        self,
        request_handler: Callable[[str | int, str, Any], Any],
        notification_handler: Callable[[str, Any], Any] | None = None,
    ) -> None:
        """Read one JSON object per line until stdin closes."""
        try:
            for raw_line in self.stdin:
                if self.closed:
                    break
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    self._dispatch(message, request_handler, notification_handler)
                except json.JSONDecodeError:
                    self.send(
                        {
                            "jsonrpc": JSONRPC_VERSION,
                            "id": None,
                            "error": {"code": -32700, "message": "Parse error"},
                        }
                    )
                except RpcError:
                    continue
                except Exception as exc:
                    print(f"ACP transport error: {exc}", file=sys.stderr, flush=True)
        finally:
            self.close()

    def _dispatch(
        self,
        message: Any,
        request_handler: Callable[[str | int, str, Any], Any],
        notification_handler: Callable[[str, Any], Any] | None,
    ) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
            raise RpcError(-32600, "Invalid Request")
        if "method" not in message:
            self._resolve_response(message)
            return
        method = message.get("method")
        if not isinstance(method, str):
            raise RpcError(-32600, "Invalid Request")
        params = message.get("params")
        if "id" not in message:
            if notification_handler is not None:
                notification_handler(method, params)
            return
        request_id = message["id"]
        try:
            result = request_handler(request_id, method, params)
            if not isinstance(result, DeferredResponse):
                self.respond(request_id, result=result)
        except RpcError as exc:
            self.respond(request_id, error=exc)
        except Exception as exc:
            print(f"ACP request {method} failed: {exc}", file=sys.stderr, flush=True)
            self.respond(request_id, error=RpcError(-32603, "Internal error", {"details": str(exc)}))

    def _resolve_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        with self._pending_lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return
        pending.result = message.get("result")
        error = message.get("error")
        pending.error = dict(error) if isinstance(error, dict) else None
        pending.event.set()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._pending_lock:
            pending = list(self._pending.values())
        for response in pending:
            response.error = {"code": -32000, "message": "ACP connection closed"}
            response.event.set()


__all__ = ["JsonRpcStdio"]
