"""Timed anime trivia rounds based on the live character catalogue."""
import asyncio
import random
import secrets
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, collection, user_collection

_SESSIONS: dict[str, dict] = {}
_CHAT_ACTIVE: dict[int, str] = {}
_ROUND_SECONDS = 35
_COIN_REWARD = 80
_XP_REWARD = 20


async def _expire(token: str, bot) -> None:
    await asyncio.sleep(_ROUND_SECONDS)
    state = _SESSIONS.pop(token, None)
    if not state:
        return
    _CHAT_ACTIVE.pop(state["chat_id"], None)
    try:
        await bot.edit_message_text(
            chat_id=state["chat_id"],
            message_id=state["message_id"],
            text=(
                "⏰ <b>TRIVIA TIME'S UP!</b>\n\n"
                f"The answer was <b>{escape(state['answer'])}</b>.\n"
                f"Character: {escape(state['character'])} ({escape(state['anime'])})"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        pass


async def trivia(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    chat_id = update.effective_chat.id
    if chat_id in _CHAT_ACTIVE:
        await message.reply_text("🧠 A trivia round is already active in this chat.")
        return

    chars = await collection.find({"name": {"$exists": True}, "anime": {"$exists": True}}).to_list(length=10000)
    if len(chars) < 4:
        await message.reply_text("Not enough character data to start trivia yet.")
        return
    correct = random.choice(chars)
    answer = str(correct.get("anime", "Unknown"))
    other = list({str(c.get("anime")) for c in chars if c.get("anime") and str(c.get("anime")) != answer})
    if len(other) < 3:
        await message.reply_text("Not enough different anime titles for a trivia round.")
        return
    options = random.sample(other, 3) + [answer]
    random.shuffle(options)
    token = secrets.token_hex(4)
    correct_index = options.index(answer)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{chr(65+i)}. {name[:48]}", callback_data=f"trivia:{token}:{i}")]
        for i, name in enumerate(options)
    ])
    sent = await message.reply_text(
        "🧠 <b>MIKASAVERSE ANIME TRIVIA</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Which anime does this character belong to?\n\n"
        f"🎴 <b>{escape(str(correct.get('name', 'Unknown')))}</b>\n"
        f"⏳ You have {_ROUND_SECONDS} seconds.\n"
        f"🏆 Reward: {_COIN_REWARD} coins + {_XP_REWARD} XP",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    _SESSIONS[token] = {
        "chat_id": chat_id,
        "message_id": sent.message_id,
        "answer": answer,
        "character": str(correct.get("name", "Unknown")),
        "anime": answer,
        "options": options,
        "correct_index": correct_index,
        "expires": time.time() + _ROUND_SECONDS,
    }
    _CHAT_ACTIVE[chat_id] = token
    asyncio.create_task(_expire(token, context.bot))


async def trivia_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    try:
        _, token, raw_index = query.data.split(":")
        index = int(raw_index)
    except (ValueError, TypeError):
        await query.answer()
        return
    state = _SESSIONS.get(token)
    if not state or time.time() > state["expires"]:
        await query.answer("⌛ This trivia round has expired.", show_alert=True)
        return
    if query.message.chat.id != state["chat_id"]:
        await query.answer("This round belongs to another chat.", show_alert=True)
        return
    if index != state["correct_index"]:
        await query.answer("Not quite! Try another answer.", show_alert=True)
        return

    _SESSIONS.pop(token, None)
    _CHAT_ACTIVE.pop(state["chat_id"], None)
    user = query.from_user
    await user_collection.update_one(
        {"id": user.id},
        {
            "$inc": {"coins": _COIN_REWARD, "xp": _XP_REWARD, "trivia_wins": 1},
            "$setOnInsert": {
                "id": user.id, "username": user.username,
                "first_name": user.first_name, "characters": [],
                "favorites": [], "wins": 0, "total_guesses": 0,
            },
        },
        upsert=True,
    )
    await query.answer("Correct! Rewards added 🎉")
    await query.edit_message_text(
        "✅ <b>CORRECT ANSWER!</b>\n\n"
        f"👑 <a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> got it!\n"
        f"🎴 {escape(state['character'])} belongs to <b>{escape(state['anime'])}</b>.\n\n"
        f"🟡 +{_COIN_REWARD} Neko Coins  ·  ✨ +{_XP_REWARD} XP",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("trivia", trivia, block=False))
application.add_handler(CallbackQueryHandler(trivia_callback, pattern=r"^trivia:[a-f0-9]+:\d+$", block=False))
