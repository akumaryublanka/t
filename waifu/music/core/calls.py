"""Voice-call facade; all state remains owned by the existing runtime."""
from waifu.music import runtime


def get_calls():
    return runtime.get_calls()


def active_chats() -> list[int]:
    return [chat_id for chat_id, state in runtime._sessions.items() if state.get("current")]


async def play(chat_id: int, track: dict) -> None:
    await runtime._start_track(chat_id, track)


async def stop(chat_id: int) -> None:
    await runtime.stop_chat(chat_id)


async def skip(chat_id: int) -> None:
    await runtime._advance_queue(chat_id, announce=True, force_skip=True)
