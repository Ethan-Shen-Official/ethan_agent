import threading
import time
from pathlib import Path

import pytest

from cli.commands import format_help, handle_repl_command
from core.errors import SessionError
from core.types import ProviderEvent
from harness.app import Harness
from providers.base import FakeProvider
from runtime.execution import LocalExecutionEnv


def test_abort_command_is_registered_and_idle_abort_is_safe(capsys):
    assert "/abort" in format_help()

    class IdleHarness:
        is_running = False

        def abort(self):
            raise AssertionError("idle abort must not call the harness")

    assert handle_repl_command("/abort", IdleHarness()) is True
    assert "no active task" in capsys.readouterr().out


def test_agent_session_allows_only_one_reserved_prompt(tmp_path: Path):
    harness = Harness(FakeProvider(["done"]), str(tmp_path))
    first = harness.prompt("first")
    assert harness.is_running is True
    with pytest.raises(SessionError, match="already running"):
        harness.prompt("second")
    first.close()
    assert harness.is_running is False


def test_abort_during_provider_stream_stops_without_next_turn(tmp_path: Path):
    started = threading.Event()
    release = threading.Event()

    class BlockingProvider:
        def stream(self, request):
            started.set()
            release.wait(5)
            yield ProviderEvent("text_delta", text="partial")
            yield ProviderEvent("done")

    harness = Harness(BlockingProvider(), str(tmp_path))
    events = []
    worker = threading.Thread(
        target=lambda: events.extend(harness.prompt("stop")), daemon=True
    )
    worker.start()
    assert started.wait(2)
    harness.abort()
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert events[-1].data["reason"] == "cancelled"
    assert not any(event.kind == "assistant_message" for event in events)


def test_local_execution_honors_cancel_event(tmp_path: Path):
    cancel = threading.Event()
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            LocalExecutionEnv(tmp_path).execute(
                'python -c "import time; time.sleep(10)"', cancel_event=cancel
            )
        ),
        daemon=True,
    )
    worker.start()
    time.sleep(0.2)
    cancel.set()
    worker.join(2)
    assert not worker.is_alive()
    assert result[0][0] == 130
    assert "command cancelled" in result[0][2]
