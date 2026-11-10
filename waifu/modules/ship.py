"""Fun player ship meter for group chats."""
import random
from datetime import datetime, timezone
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection


async def ship(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("Reply to someone's message with <code>/ship</code> to calculate your compatibility.", parse_mode=ParseMode.HTML)
        return
    one = update.effective_user
    two = message.reply_to_message.from_user
    if one.id == two.id:
        await message.reply_text("💔 You can't ship yourself with yourself!")
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    low, high = sorted((one.id, two.id))
    score = random.Random(f"{low}:{high}:{day}").randint(1, 100)
    hearts = "❤️" * max(1, round(score / 10)) + "🖤" * (10 - max(1, round(score / 10)))
    if score >= 90:
        verdict = "Soulmates forever! 💍"
    elif score >= 70:
        verdict = "A lovely anime romance! 🌸"
    elif score >= 40:
        verdict = "A slow-burn story... 💜"
    else:
        verdict = "Rivals-to-lovers? Or just rivals? 😂"
    first = escape(one.first_name or "Player")
    second = escape(two.first_name or "Player")
    a = await user_collection.find_one({"id": one.id}) or {}
    b = await user_collection.find_one({"id": two.id}) or {}
    combined = len(a.get("characters", [])) + len(b.get("characters", []))
    await message.reply_text(
        "💘 <b>MIKASAVERSE SHIP METER</b>\n\n"
        f"<a href='tg://user?id={one.id}'>{first}</a>  ×  "
        f"<a href='tg://user?id={two.id}'>{second}</a>\n\n"
        f"{hearts}\n<b>{score}% Compatibility</b>\n"
        f"{verdict}\n\n🎴 Combined collection: <b>{combined}</b>",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("ship", ship, block=False))
