from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .types import Message, StopReason


@dataclass
class LoopState:
    messages: list[Message] = field(default_factory=list)
    turn_count: int = 0
    total_tokens: int = 0
    stop_reason: StopReason | None = None
    cancelled: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    recovery_failures: int = 0

    def begin_run(self) -> None:
        """Reset per-prompt execution state while retaining the transcript and usage."""
        self.turn_count = 0
        self.stop_reason = None
        self.recovery_failures = 0
        self.cancelled = False
        self.cancel_event.clear()

    def request_cancel(self) -> None:
        self.cancelled = True
        self.cancel_event.set()
