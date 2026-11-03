"""
modules/couple.py

/couple — Shows a random couple of the day from users in the same group.
"""
import random
from html import escape
from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, group_user_totals_collection, user_collection


async def couple(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type == "private":
        await update.message.reply_text("💔 This command only works in groups!")
        return

    chat_id = update.effective_chat.id

    # Get users who have been active in this group
    group_users = await group_user_totals_collection.find(
        {"group_id": chat_id}
    ).to_list(length=1000)

    if not group_users or len(group_users) < 2:
        await update.message.reply_text(
            "💔 Not enough users in this group yet!\n"
            "More people need to play first! 🌸"
        )
        return

    # Pick 2 random unique users using today's date as seed
    today = datetime.now().strftime("%Y-%m-%d")
    random.seed(today + str(chat_id))
    picked = random.sample(group_users, 2)
    random.seed()

    uid1 = picked[0]["user_id"]
    uid2 = picked[1]["user_id"]

    # Get full user docs for character count
    user1 = await user_collection.find_one({"id": uid1})
    user2 = await user_collection.find_one({"id": uid2})

    name1 = escape(picked[0].get("first_name", "Unknown"))
    name2 = escape(picked[1].get("first_name", "Unknown"))

    chars1   = len(user1.get("characters", [])) if user1 else 0
    chars2   = len(user2.get("characters", [])) if user2 else 0
    combined = chars1 + chars2

    text = (
        f"✨ 💕 <b>Today's Special Couple</b> 💕 ✨\n\n"
        f"🌸 <a href='tg://user?id={uid1}'>{name1}</a>  ×  "
        f"<a href='tg://user?id={uid2}'>{name2}</a> 🌸\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💘 Love Level: MAX\n"
        f"🎴 Combined Waifus: {combined}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"💍 Forever Shipped! 🌸"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("couple", couple, block=False))
