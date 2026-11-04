"""
NekoVerse Word Scramble — guess a character name from the catalogue.
The first correct guess in a chat wins the round.
"""
import asyncio
import random
import re
import time
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import application, collection, user_collection

_sessions: dict[int, dict] = {}
_last_started: dict[int, float] = {}
_ROUND_SECONDS = 60
_START_COOLDOWN = 20
_REWARD_COINS = 35
_REWARD_XP = 12


def _normalise(value: str) -> str:
    return "".join(char.casefold() for char in value if char.isalnum())


def _scramble_name(name: str) -> str:
    words = name.split()
    scrambled_words = []
    changed = False

    for word in words:
        letters = list(word)
        if len(letters) > 2:
            original = "".join(letters)
            for _ in range(20):
                random.shuffle(letters)
                candidate = "".join(letters)
                if candidate.casefold() != original.casefold():
                    changed = True
                    break
            scrambled_words.append(candidate if candidate.casefold() != original.casefold() else original)
        else:
            scrambled_words.append(word)

    scrambled = " ".join(scrambled_words)
    if changed and _normalise(scrambled) != _normalise(name):
        return scrambled.upper()

    letters = [char for char in name if char.isalnum()]
    for _ in range(30):
        random.shuffle(letters)
        candidate = "".join(letters)
        if candidate.casefold() != _normalise(name):
            return candidate.upper()
    return scrambled.upper()


async def _expire_round(chat_id: int, session: dict, bot) -> None:
    await asyncio.sleep(_ROUND_SECONDS)
    if _sessions.get(chat_id) is not session:
        return
    _sessions.pop(chat_id, None)
    await bot.send_message(
        chat_id,
        "⏰ <b>Time's up!</b>\n"
        f"The character was <b>{escape(session['name'])}</b> from "
        f"<i>{escape(session['anime'])}</i>.\n"
        "Start another round with /scramble.",
        parse_mode=ParseMode.HTML,
    )


async def scramble(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return

    if chat.id in _sessions:
        await message.reply_text("🧩 A Word Scramble round is already active here. Guess the answer or use /scramble_stop.")
        return

    remaining = _START_COOLDOWN - (time.monotonic() - _last_started.get(chat.id, 0))
    if remaining > 0:
        await message.reply_text(f"⏳ Try starting another round in {int(remaining) + 1}s.")
        return

    characters = await collection.aggregate([
        {"$match": {"name": {"$type": "string"}, "anime": {"$type": "string"}}},
        {"$sample": {"size": 1}},
    ]).to_list(length=1)

    if not characters:
        await message.reply_text("❌ The character catalogue is empty. Add characters before starting Scramble.")
        return

    character = characters[0]
    name = str(character.get("name", "")).strip()
    anime = str(character.get("anime", "")).strip()
    if not name or not anime:
        await message.reply_text("❌ I couldn't find a usable character. Please try again.")
        return

    scrambled = _scramble_name(name)
    if _normalise(scrambled) == _normalise(name):
        await message.reply_text("❌ I couldn't scramble that name. Please start another round.")
        return

    session = {
        "name": name,
        "anime": anime,
        "starter_id": user.id,
        "attempts": 0,
        "scrambled": scrambled,
    }
    _sessions[chat.id] = session
    _last_started[chat.id] = time.monotonic()

    await message.reply_text(
        "🧩 <b>NEKOVERSE WORD SCRAMBLE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📺 Anime hint: <b>{escape(anime)}</b>\n"
        f"🔤 Unscramble this name:\n<code>{escape(scrambled)}</code>\n\n"
        "The first player to guess correctly wins "
        f"<b>🟡 {_REWARD_COINS} Neko Coins + {_REWARD_XP} XP</b>.\n"
        f"⏱ You have {_ROUND_SECONDS} seconds.\n"
        "Anyone in this chat can answer!",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(_expire_round(chat.id, session, context.bot))


async def scramble_message(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or not message.text:
        return

    session = _sessions.get(chat.id)
    if not session:
        return

    if _normalise(message.text) != _normalise(session["name"]):
        session["attempts"] += 1
        if session["attempts"] in (3, 6):
            await message.reply_text(
                f"💡 Hint: the name has {len(_normalise(session['name']))} letters."
            )
        return

    # Remove the active game before awaiting database/network work, so only one
    # player can claim the reward if multiple correct guesses arrive together.
    _sessions.pop(chat.id, None)
    await user_collection.update_one(
        {"id": user.id},
        {
            "$inc": {
                "coins": _REWARD_COINS,
                "xp": _REWARD_XP,
                "scramble_wins": 1,
            },
            "$set": {"username": user.username, "first_name": user.first_name},
            "$setOnInsert": {
                "characters": [], "wins": 0, "total_guesses": 0, "favorites": [],
            },
        },
        upsert=True,
    )
    await message.reply_text(
        "🎉 <b>Correct answer!</b>\n"
        f"🏆 <a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> guessed "
        f"<b>{escape(session['name'])}</b>!\n"
        f"📺 {escape(session['anime'])}\n\n"
        f"🟡 +{_REWARD_COINS} Neko Coins  ✨ +{_REWARD_XP} XP",
        parse_mode=ParseMode.HTML,
    )


async def scramble_stop(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return

    session = _sessions.get(chat.id)
    if not session:
        await message.reply_text("There is no active Word Scramble round here.")
        return
    if user.id != session["starter_id"]:
        await message.reply_text("Only the player who started this round can stop it.")
        return

    _sessions.pop(chat.id, None)
    await message.reply_text(
        "🛑 <b>Word Scramble stopped.</b>\n"
        f"The answer was <b>{escape(session['name'])}</b> from "
        f"<i>{escape(session['anime'])}</i>.",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("scramble", scramble, block=False))
application.add_handler(CommandHandler("scramble_stop", scramble_stop, block=False))
application.add_handler(MessageHandler(
    filters.TEXT & ~filters.COMMAND,
    scramble_message,
    block=False,
), group=2)
