"""Small JSON-RPC/ACP protocol primitives used by the stdio bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JSONRPC_VERSION = "2.0"
ACP_PROTOCOL_VERSION = 1


class RpcError(Exception):
    """An error that can be returned as a JSON-RPC response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = data


@dataclass(frozen=True)
class DeferredResponse:
    """Marker returned by a handler that will send its response later."""

    request_id: str | int


def error_payload(error: RpcError) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.data is not None:
        payload["data"] = error.data
    return payload


__all__ = [
    "ACP_PROTOCOL_VERSION",
    "DeferredResponse",
    "JSONRPC_VERSION",
    "RpcError",
    "error_payload",
]
