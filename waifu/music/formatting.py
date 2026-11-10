"""Small formatting and Telegram reply helpers for music commands."""

from telegram import Update
from telegram.constants import ParseMode


def duration(seconds: int | None) -> str:
    if seconds is None:
        return "Live / unknown"
    minutes, seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def group_only(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in ("group", "supergroup"))


async def reply(update: Update, text: str) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
