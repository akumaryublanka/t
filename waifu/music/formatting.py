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
    toggle = "resume" if paused else "pause"
    toggle_label = "▶️" if paused else "⏸️"
    autoplay_label = "🎧🟢" if autoplay else "🎧🔴"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(toggle_label, callback_data=f"mv:{toggle}:{chat_id}", style="success" if paused else "primary"),
                InlineKeyboardButton("⏭️", callback_data=f"mv:skip:{chat_id}", style="primary"),
            ],
            [
                InlineKeyboardButton("🔁", callback_data=f"mv:replay:{chat_id}", style="success"),
                InlineKeyboardButton("🔂", callback_data=f"mv:loop:{chat_id}", style="primary"),
                InlineKeyboardButton("⏹️", callback_data=f"mv:end:{chat_id}", style="danger"),
            ],
            [
                InlineKeyboardButton("📃", callback_data=f"mv:queue:{chat_id}", style="primary"),
                InlineKeyboardButton("❤️", callback_data=f"mv:fav:{chat_id}", style="success"),
            ],
            [
                InlineKeyboardButton("✨", callback_data=f"mv:mix:{chat_id}", style="primary"),
                InlineKeyboardButton(autoplay_label, callback_data=f"mv:autoplay:{chat_id}", style="success" if autoplay else "danger"),
            ],
        ]
    )


async def reply(update: Update, text: str, reply_markup=None) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
