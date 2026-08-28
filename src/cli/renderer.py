from core.types import AgentEvent


def render(event: AgentEvent) -> None:
    if event.kind == "text_delta":
        print(event.data.get("text", ""), end="", flush=True)
    elif event.kind == "tool_start":
        print(f"\n[tool] {event.data['name']} {event.data['arguments']}")
    elif event.kind == "tool_result":
        result = event.data["result"]
        print(f"[result] {result.content}")
    elif event.kind == "turn_end":
        print(f"\n[{event.data['reason']}]")
    elif event.kind == "error":
        print(f"\n[error] {event.data['message']}")
