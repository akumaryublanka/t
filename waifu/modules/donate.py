"""
modules/donate.py

/donate @user character_id — Owner can donate a character to any user.
"""
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection, OWNER_ID


async def donate(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id

    # Owner only
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ Only the owner can donate characters!")
        return

    # Must reply to a user
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Usage: Reply to a user + <code>/donate &lt;character_id&gt;</code>\n"
            "Example: Reply + <code>/donate 0001</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Character ID missing!\n"
            "Usage: Reply to a user + <code>/donate &lt;character_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target = update.message.reply_to_message.from_user
    if target.is_bot:
        await update.message.reply_text("❌ Can't donate to a bot!")
        return

    char_id = context.args[0].strip()

    # Find character in DB
    char = await collection.find_one({"id": char_id})
    if not char:
        await update.message.reply_text(
            f"❌ No character found with ID <code>{escape(char_id)}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Add character to target user
    await user_collection.update_one(
        {"id": target.id},
        {
            "$push": {"characters": char},
            "$set":  {"username": target.username, "first_name": target.first_name},
            "$setOnInsert": {
                "coins":     0,
                "xp":        0,
                "wins":      0,
                "favorites": [],
            },
        },
        upsert=True,
    )

    # Success message
    caption = (
        f"🎁 <b>Character Donated!</b>\n\n"
        f"🌸 <b>Name:</b> {escape(char['name'])}\n"
        f"📺 <b>Anime:</b> {escape(char['anime'])}\n"
        f"💎 <b>Rarity:</b> {char.get('rarity', 'Unknown')}\n"
        f"🆔 <b>ID:</b> <code>{escape(char['id'])}</code>\n\n"
        f"✅ Donated to <a href='tg://user?id={target.id}'>{escape(target.first_name)}</a>!"
    )

    photo = char.get("img_url")
    if photo:
        try:
            await update.message.reply_photo(
                photo=photo,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            await update.message.reply_text(caption, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("donate", donate, block=False))
