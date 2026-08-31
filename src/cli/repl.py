"""Interactive REPL scheduling and single-stdin coordination."""

from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Queue
import threading
from typing import TYPE_CHECKING

from harness.approval import PromptApprovalHandler
from .commands import handle_repl_command, is_exit_command, resolve_command
from .renderer import render

if TYPE_CHECKING:
    from harness.app import Harness


@dataclass
class _ApprovalRequest:
    event: threading.Event = field(default_factory=threading.Event)
    answer: bool = False


class ApprovalBroker:
    """Route permission answers through the REPL's single stdin reader."""

    def __init__(self) -> None:
        self._pending: Queue[_ApprovalRequest] = Queue()
        self._pending_event = threading.Event()

    def ask(self, prompt: str) -> str:
        request = _ApprovalRequest()
        self._pending_event.set()
        self._pending.put(request)
        request.event.wait()
        return "y" if request.answer else "n"

    def submit(self, answer: str) -> bool:
        try:
            request = self._pending.get_nowait()
        except Empty:
            return False
        if self._pending.empty():
            self._pending_event.clear()
        request.answer = answer.strip().lower() in {"y", "yes"}
        request.event.set()
        return True

    def cancel(self) -> None:
        while True:
            try:
                request = self._pending.get_nowait()
            except Empty:
                self._pending_event.clear()
                return
            request.answer = False
            request.event.set()

    @property
    def pending(self) -> bool:
        return self._pending_event.is_set()


def create_approval_handler(broker: ApprovalBroker) -> PromptApprovalHandler:
    # The raw TUI renders the request itself. Printing from the worker would
    # race with full-screen redraws and make the prompt disappear.
    return PromptApprovalHandler(broker.ask, display=False)


def run_repl(harness: "Harness", approval_broker: ApprovalBroker | None = None) -> int:
    """Run a responsive REPL while at most one prompt worker is active."""
    broker = approval_broker or ApprovalBroker()
    commands: Queue[str | None] = Queue()
    events: Queue[tuple[str, object]] = Queue()

    def read_commands() -> None:
        while True:
            try:
                value = input("agent> ")
            except (EOFError, KeyboardInterrupt):
                commands.put(None)
                return
            commands.put(value)

    reader = threading.Thread(target=read_commands, name="agent-input", daemon=True)
    reader.start()
    worker: threading.Thread | None = None

    def start_prompt(text: str) -> threading.Thread:
        def run() -> None:
            try:
                for event in harness.prompt(text):
                    events.put(("event", event))
            except BaseException as exc:
                events.put(("error", exc))
            finally:
                events.put(("done", None))

        task = threading.Thread(target=run, name="agent-task", daemon=True)
        task.start()
        return task

    def drain_events() -> None:
        while True:
            try:
                kind, value = events.get_nowait()
            except Empty:
                return
            if kind == "event":
                render(value)
            elif kind == "error":
                print(f"\n[error] {value}")

    def stop_worker() -> None:
        # Release a possible permission prompt before waiting for the task.
        broker.cancel()
        harness.abort()
        if worker is not None:
            worker.join()

    try:
        while True:
            drain_events()
            if worker is not None and not worker.is_alive() and not harness.is_running:
                worker = None

            try:
                command = commands.get(timeout=0.05)
            except Empty:
                continue

            if command is None:
                if harness.is_running:
                    stop_worker()
                return 0
            if command.strip() == "":
                continue

            if is_exit_command(command):
                if harness.is_running:
                    stop_worker()
                return 0

            if harness.is_running:
                spec = resolve_command(command.split()[0]) if command.split() else None
                if spec is not None and spec.name == "/abort":
                    broker.cancel()
                    handle_repl_command(command, harness)
                elif broker.pending:
                    if not broker.submit(command):
                        print("[busy] agent is still running; use /abort")
                else:
                    print("[busy] agent is still running; use /abort")
                continue

            if handle_repl_command(command, harness):
                continue
            worker = start_prompt(command)
    finally:
        broker.cancel()


__all__ = ["ApprovalBroker", "create_approval_handler", "run_repl"]
