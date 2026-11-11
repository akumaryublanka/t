"""Formatting helpers and interactive player controls."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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


def player_keyboard(
    chat_id: int,
    paused: bool = False,
    autoplay: bool = False,
) -> InlineKeyboardMarkup:
    """Screenshot-matched four-row music player controls."""
    autoplay_label = "🔄 AUTOPLAY ✅" if autoplay else "🔄 AUTOPLAY ❌"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("▶️", callback_data=f"mv:resume:{chat_id}", style="primary"),
                InlineKeyboardButton("⏸️", callback_data=f"mv:pause:{chat_id}", style="primary"),
                InlineKeyboardButton("🔁", callback_data=f"mv:replay:{chat_id}", style="primary"),
                InlineKeyboardButton("⏭️", callback_data=f"mv:skip:{chat_id}", style="primary"),
                InlineKeyboardButton("⏹️", callback_data=f"mv:end:{chat_id}", style="danger"),
            ],
            [
                InlineKeyboardButton("➕ PLAYLIST", callback_data=f"mv:queue:{chat_id}", style="success"),
                InlineKeyboardButton(autoplay_label, callback_data=f"mv:autoplay:{chat_id}", style="success" if autoplay else "primary"),
            ],
            [
                InlineKeyboardButton("✨ GET SUGGESTIONS", callback_data=f"mv:mix:{chat_id}", style="success"),
            ],
            [
                InlineKeyboardButton("🗑 CLOSE", callback_data=f"mv:close:{chat_id}", style="success"),
            ],
        ]
    )

async def reply(update: Update, text: str, reply_markup=None) -> None:
    message = update.effective_message
    if message:
        await message.get_bot().send_message(
            chat_id=message.chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
