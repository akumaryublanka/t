"""Queue helpers backed by the runtime's per-chat queue state."""
from waifu.music import runtime


def get_queue(chat_id: int) -> list[dict]:
    state = runtime._sessions.get(chat_id) or {}
    return list(state.get("queue", []))


def get_current(chat_id: int) -> dict | None:
    state = runtime._sessions.get(chat_id) or {}
    return state.get("current")


def clear_queue(chat_id: int) -> int:
    state = runtime._sessions.get(chat_id)
    if not state:
        return 0
    removed = len(state.get("queue", []))
    state["queue"].clear()
    return removed
