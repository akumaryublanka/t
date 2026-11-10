"""Broadcast a music status notice to a selected set of active chats."""
from waifu import application


async def broadcast_to_chats(chat_ids: list[int], message: str) -> tuple[int, int]:
    sent = failed = 0
    for chat_id in dict.fromkeys(chat_ids):
        try:
            await application.bot.send_message(chat_id=chat_id, text=message, disable_web_page_preview=True)
            sent += 1
        except Exception:
            failed += 1
    return sent, failed
