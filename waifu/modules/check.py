"""
modules/check.py

/check <id> — Check character details by ID.
"""
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application
from waifu import collection as anime_collection


async def check(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text(
            "📋 Usage: <code>/check &lt;character_id&gt;</code>\n"
            "Example: <code>/check 0001</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id = context.args[0].strip()

    try:
        char = await anime_collection.find_one({"id": char_id})
    except Exception as e:
        await update.message.reply_text(f"❌ Database error: {e}")
        return

    if not char:
        await update.message.reply_text(
            f"❌ No character found with ID <code>{escape(char_id)}</code>.\n"
            f"Make sure the ID is correct!",
            parse_mode=ParseMode.HTML,
        )
        return

    caption = (
        f"🎴 <b>Character Info</b>\n\n"
        f"🌸 <b>Name:</b> {escape(char['name'])}\n"
        f"📺 <b>Anime:</b> {escape(char['anime'])}\n"
        f"💎 <b>Rarity:</b> {char.get('rarity', 'Unknown')}\n"
        f"🆔 <b>ID:</b> <code>{escape(char['id'])}</code>"
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


application.add_handler(CommandHandler("check", check, block=False))
              
