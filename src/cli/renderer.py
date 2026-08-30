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
    elif event.kind == "compaction_start":
        print("\n[compaction] summarizing context...", flush=True)
    elif event.kind == "compaction_end":
        if event.data.get("is_error"):
            print(f"[compaction error] {event.data.get('error', 'unknown error')}")
        else:
            print("[compaction] context summary saved")
