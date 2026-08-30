from core.context import DefaultContextBuilder
from core.loop import AgentLoop
from core.state import LoopState
from core.types import Message, ModelRequest, ToolSpec
from harness.inspection import ContextInspector, InspectingProvider, format_context_snapshot
from providers.base import FakeProvider
from runtime.execution import LocalExecutionEnv
from runtime.permissions import AllowAllPermissions
from tools.base import ToolContext
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def _loop(provider):
    context = ToolContext(LocalExecutionEnv("."), AllowAllPermissions())
    return AgentLoop(
        provider,
        ToolExecutor(ToolRegistry(), context),
        context_builder=DefaultContextBuilder(system_prompt="base prompt"),
    )


def test_inspecting_provider_captures_exact_request_without_loop_changes():
    inspector = ContextInspector()
    provider = InspectingProvider(FakeProvider(["done"]), inspector)

    events = list(_loop(provider).run("inspect this"))

    assert events[-1].data["reason"] == "completed"
    snapshot = inspector.snapshot()
    assert snapshot is not None
    assert snapshot.sequence == 1
    assert snapshot.request.system_prompt == "base prompt"
    assert [message.content for message in snapshot.request.messages] == ["inspect this"]


def test_snapshot_is_defensive_and_default_rendering_redacts_secrets():
    inspector = ContextInspector()
    request = ModelRequest(
        messages=(Message.user("API_KEY=hidden-value"),),
        tools=(ToolSpec("demo", "Authorization: hidden-value", {"secret": {"type": "string"}}),),
        system_prompt="Authorization: Bearer hidden-value",
    )
    inspector.capture(request)
    request.messages[0].content = "mutated after capture"

    snapshot = inspector.snapshot()
    assert snapshot is not None
    assert snapshot.request.messages[0].content == "API_KEY=hidden-value"
    safe = format_context_snapshot(snapshot)
    assert "hidden-value" not in safe
    assert "[REDACTED]" in safe
    raw = format_context_snapshot(snapshot, redact=False)
    assert "hidden-value" in raw


def test_show_context_command_uses_latest_snapshot(capsys):
    from cli.main import handle_repl_command

    inspector = ContextInspector()
    inspector.capture(ModelRequest((Message.user("hello"),), (), "system"))

    class HarnessStub:
        def context_snapshot(self):
            return inspector.snapshot()

    assert handle_repl_command("/show_context", HarnessStub()) is True
    output = capsys.readouterr().out
    assert '"system_prompt": "system"' in output
    assert '"hello"' in output


def test_show_context_command_reports_empty_snapshot(capsys):
    from cli.main import handle_repl_command

    class HarnessStub:
        def context_snapshot(self):
            return None

    assert handle_repl_command("/show_context", HarnessStub()) is True
    assert "no model request has been sent yet" in capsys.readouterr().out


def test_harness_exposes_the_request_after_a_real_prompt():
    from harness.app import Harness

    class MemorySessionStore:
        def __init__(self):
            self.messages = []

        def append(self, message):
            self.messages.append(message)

        def read(self):
            return list(self.messages)

    harness = Harness(
        FakeProvider(["done"]),
        ".",
        session_store=MemorySessionStore(),
    )
    list(harness.prompt("show me the context"))

    snapshot = harness.context_snapshot()
    assert snapshot is not None
    assert snapshot.request.messages[0].content == "show me the context"
    assert "Runtime context:" in snapshot.request.system_prompt
