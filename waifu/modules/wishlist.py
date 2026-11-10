"""Character wishlist with private notifications when a wished character drops."""
import re
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection


async def _find_character(query: str):
    value = query.strip().replace("-", " ")
    if not value:
        return None
    found = await collection.find_one({"id": value})
    if found:
        return found
    exact = await collection.find_one({"name": {"$regex": "^" + re.escape(value) + "$", "$options": "i"}})
    if exact:
        return exact
    return await collection.find_one({"name": {"$regex": re.escape(value), "$options": "i"}})


async def wish(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not context.args:
        await message.reply_text("Usage: <code>/wish character-name</code>", parse_mode=ParseMode.HTML)
        return
    char = await _find_character(" ".join(context.args))
    if not char:
        await message.reply_text("❌ I couldn't find that character in the catalogue.")
        return

    char_id = str(char.get("id", ""))
    player = await user_collection.find_one({"id": user.id}) or {}
    wishlist = [str(x) for x in player.get("wishlist", [])]
    if char_id in wishlist:
        await message.reply_text(f"⭐ <b>{escape(char.get('name', 'Character'))}</b> is already on your wishlist.", parse_mode=ParseMode.HTML)
        return
    if len(wishlist) >= 20:
        await message.reply_text("Your wishlist is full (20 characters). Remove one with /unwish first.")
        return

    await user_collection.update_one(
        {"id": user.id},
        {"$addToSet": {"wishlist": char_id},
         "$setOnInsert": {"id": user.id, "username": user.username, "first_name": user.first_name, "characters": [], "coins": 0}},
        upsert=True,
    )
    await message.reply_text(
        f"⭐ Added <b>{escape(char.get('name', 'Character'))}</b> to your wishlist!\n"
        f"🎬 {escape(char.get('anime', 'Unknown'))}\n"
        f"🔔 I'll send you a private alert when this character appears in a group drop.",
        parse_mode=ParseMode.HTML,
    )


async def wishlist(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    player = await user_collection.find_one({"id": user.id}) or {}
    ids = [str(x) for x in player.get("wishlist", [])]
    if not ids:
        await update.effective_message.reply_text("⭐ Your wishlist is empty. Add characters with <code>/wish name</code>.", parse_mode=ParseMode.HTML)
        return
    chars = await collection.find({"id": {"$in": ids}}).to_list(length=100)
    by_id = {str(c.get("id")): c for c in chars}
    lines = ["⭐ <b>YOUR WISHLIST</b>", "━━━━━━━━━━━━━━━━━━"]
    for index, char_id in enumerate(ids, 1):
        char = by_id.get(char_id)
        if char:
            lines.append(f"{index}. <b>{escape(char.get('name', 'Unknown'))}</b> — {escape(char.get('anime', 'Unknown'))}  <code>{escape(char_id)}</code>")
        else:
            lines.append(f"{index}. <code>{escape(char_id)}</code> — catalogue entry unavailable")
    lines.append("\nRemove a character with <code>/unwish name-or-id</code>.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def unwish(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not context.args:
        await update.effective_message.reply_text("Usage: <code>/unwish character-name</code>", parse_mode=ParseMode.HTML)
        return
    char = await _find_character(" ".join(context.args))
    if not char:
        await update.effective_message.reply_text("❌ Character not found.")
        return
    char_id = str(char.get("id", ""))
    result = await user_collection.update_one({"id": user.id}, {"$pull": {"wishlist": char_id}})
    if not result.modified_count:
        await update.effective_message.reply_text("That character wasn't on your wishlist.")
        return
    await update.effective_message.reply_text(
        f"Removed <b>{escape(char.get('name', 'Character'))}</b> from your wishlist.",
        parse_mode=ParseMode.HTML,
    )


async def notify_wishlist_match(bot, char: dict) -> None:
    """Send best-effort private alerts; closed DMs never interrupt a drop."""
    char_id = str(char.get("id", ""))
    if not char_id:
        return
    users = await user_collection.find(
        {"wishlist": char_id, "characters.id": {"$ne": char_id}},
        {"id": 1, "first_name": 1},
    ).limit(50).to_list(length=50)
    text = (
        "🔔 <b>WISHLIST ALERT — MIKASAVERSE</b>\n\n"
        f"⭐ <b>{escape(str(char.get('name', 'Unknown')))}</b>\n"
        f"🎬 {escape(str(char.get('anime', 'Unknown')))}\n"
        f"✨ {escape(str(char.get('rarity', 'Character')))}\n\n"
        "This character just appeared in a group drop. Open your active game group and try /guess!"
    )
    for player in users:
        try:
            await bot.send_message(chat_id=int(player["id"]), text=text, parse_mode=ParseMode.HTML)
        except Exception:
            # Users who have not started the bot or blocked DMs are skipped.
            continue


application.add_handler(CommandHandler("wish", wish, block=False))
application.add_handler(CommandHandler("wishlist", wishlist, block=False))
application.add_handler(CommandHandler("unwish", unwish, block=False))
