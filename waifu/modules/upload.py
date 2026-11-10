"""
modules/upload.py

Two upload methods:

1) /upload character-name anime-name rarity_number
   Reply to an image; the bot uses the replied image directly.

2) /uploadchar  (reply to a channel post whose caption is in the format):
   🍀 Name: Sasha Braus
   🍋 Rarity: Legendary
   🌸 Anime: Attack On Titan
   🌱 ID: 26          ← optional; auto-generated if absent

   The replied-to message must have a photo attached.

Both methods post to CHARA_CHANNEL_ID and insert into the DB.
"""
import os
import re

import aiohttp
from pymongo import ReturnDocument
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, db, sudo_users, OWNER_ID, CHARA_CHANNEL_ID
from waifu.config import Config
from waifu.modules.auto_delete import delete_now

RARITY_MAP  = Config.RARITY_MAP
RARITY_STRS = {v.lower(): v for v in RARITY_MAP.values()}  # e.g. "legendary" → "🟡 Legendary"
RARITY_ALIASES = {
    label.casefold(): canonical
    for label, canonical in Config.RARITY_LEGACY_MAP.items()
}
RARITY_ALIASES.update({"🎄 christmas": RARITY_MAP[13], "christmas": RARITY_MAP[13]})

WRONG_FORMAT = (
    "❌ Wrong format\n\n"
    "Reply to an image and send:\n"
    "<code>/upload character-name anime-name rarity_number</code>\n\n"
    "<b>Main rarities:</b>\n"
    + "\n".join(f"  {k} → {RARITY_MAP[k]}" for k in sorted(RARITY_MAP) if k <= 10)
    + "\n\n<b>🎉 Event rarities:</b>\n"
    + "\n".join(f"  {k} → {RARITY_MAP[k]}" for k in sorted(RARITY_MAP) if k >= 11)
)


def _is_sudo(uid: int) -> bool:
    return uid in sudo_users or uid == OWNER_ID


async def _validate_url(url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                return r.status < 400
    except Exception:
        return False


async def _next_id() -> str:
    doc = await db.sequences.find_one_and_update(
        {"_id": "character_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return str(doc["sequence_value"]).zfill(4)


def _char_caption(char: dict, uploader_id: int, uploader_name: str) -> str:
    return (
        f"🍀 <b>Name:</b> {char['name']}\n"
        f"🍋 <b>Rarity:</b> {char['rarity']}\n"
        f"🌸 <b>Anime:</b> {char['anime']}\n"
        f"🌱 <b>ID:</b> {char['id']}\n\n"
        f"Added by <a href='tg://user?id={uploader_id}'>{uploader_name}</a>"
    )


# ── Method 1: /upload ─────────────────────────────────────────────────────────

async def upload(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    if len(context.args) != 3:
        await update.message.reply_text(WRONG_FORMAT, parse_mode=ParseMode.HTML)
        return

    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text(
            "❌ Pehle image send karo, phir us image par reply karke command bhejo.\n\n"
            "<code>/upload character-name anime-name rarity_number</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if replied.photo:
        source_file_id = replied.photo[-1].file_id
        filename, content_type = "character.jpg", "image/jpeg"
    elif replied.document and replied.document.mime_type and replied.document.mime_type.startswith("image/"):
        source_file_id = replied.document.file_id
        filename = replied.document.file_name or "character-image"
        content_type = replied.document.mime_type
    else:
        await update.message.reply_text("❌ Reply ki hui message mein image/photo honi chahiye.")
        return

    raw_name, raw_anime, raw_rarity = context.args

    try:
        rarity = RARITY_MAP[int(raw_rarity)]
    except (KeyError, ValueError):
        await update.message.reply_text(
            f"❌ Invalid rarity number. Choose from: {', '.join(map(str, RARITY_MAP))}.", parse_mode=ParseMode.HTML)
        return

    name    = raw_name.replace("-", " ").title()
    anime   = raw_anime.replace("-", " ").title()
    char_id = await _next_id()

    try:
        # Telegram file_id is served to the Mini App through the existing backend image proxy.
        # Do not depend on ImgBB: it may block uploads with HTTP 400.
        char = {"img_url": source_file_id, "name": name, "anime": anime,
                "rarity": rarity, "id": char_id}
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=source_file_id,
            caption=_char_caption(char, update.effective_user.id, update.effective_user.first_name),
            parse_mode=ParseMode.HTML,
        )
        # Store the channel-post photo file_id; the Mini App proxy serves it to browsers.
        char["message_id"] = msg.message_id
        # Save the photo file_id produced by the channel photo post.
        # This also normalizes images originally received as image documents.
        if msg.photo:
            char["img_url"] = msg.photo[-1].file_id
        await collection.insert_one(char)
        await update.message.reply_text(
            f"✅ <b>{name}</b> added!  ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Channel post failed: {e}\nCharacter was <b>not</b> saved.",
            parse_mode=ParseMode.HTML,
        )


# ── Method 2: /uploadchar (reply to formatted post) ──────────────────────────

def _parse_caption(caption: str) -> dict | None:
    """
    Parse a caption like:
        🍀 Name: Sasha Braus
        🍋 Rarity: Legendary
        🌸 Anime: Attack On Titan
        🌱 ID: 26
    Returns dict with keys: name, rarity, anime, id (id may be None).
    """
    fields: dict[str, str] = {}
    patterns = {
        "name":   r"(?:🍀\s*)?Name\s*:\s*(.+)",
        "rarity": r"(?:🍋\s*)?Rarity\s*:\s*(.+)",
        "anime":  r"(?:🌸\s*)?Anime\s*:\s*(.+)",
        "id":     r"(?:🌱\s*)?ID\s*:\s*(\S+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, caption, re.IGNORECASE)
        if m:
            fields[key] = m.group(1).strip()

    if "name" not in fields or "anime" not in fields:
        return None

    raw_rarity = fields.get("rarity", "").lower()
    rarity = RARITY_STRS.get(raw_rarity) or RARITY_ALIASES.get(raw_rarity)
    if not rarity:
        for key, val in RARITY_STRS.items():
            if raw_rarity in key or key in raw_rarity:
                rarity = val
                break
    if not rarity:
        rarity = "⚪ Common"

    return {
        "name": fields["name"].title(),
        "anime": fields["anime"].title(),
        "rarity": rarity,
        "id": fields.get("id"),
    }


async def uploadchar(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text(
            "❌ Reply to a post that has the character's image and caption.\n\n"
            "<b>Expected caption format:</b>\n"
            "🍀 Name: Character Name\n"
            "🍋 Rarity: Limited Edition\n"
            "🌸 Anime: Anime Name\n"
            "🌱 ID: 26  <i>(optional)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    photo = None
    filename, content_type = "character.jpg", "image/jpeg"
    if replied.photo:
        photo = replied.photo[-1].file_id
    elif replied.document and replied.document.mime_type and replied.document.mime_type.startswith("image/"):
        photo = replied.document.file_id
        filename = replied.document.file_name or "character-image"
        content_type = replied.document.mime_type
    else:
        await update.message.reply_text("❌ The replied message must contain an image.")
        return

    caption = replied.caption or replied.text or ""
    parsed = _parse_caption(caption)
    if not parsed:
        await update.message.reply_text(
            "❌ Could not parse the caption. Make sure it contains at least "
            "<b>Name</b> and <b>Anime</b> fields.",
            parse_mode=ParseMode.HTML,
        )
        return

    if parsed["id"]:
        existing = await collection.find_one({"id": parsed["id"]})
        if existing:
            await update.message.reply_text(
                f"❌ ID <code>{parsed['id']}</code> already exists in the database.",
                parse_mode=ParseMode.HTML,
            )
            return
        char_id = parsed["id"]
    else:
        char_id = await _next_id()

    try:
        # Store Telegram file_id; the existing backend image proxy serves it to browsers.
        char = {
            "img_url": photo,
            "name": parsed["name"],
            "anime": parsed["anime"],
            "rarity": parsed["rarity"],
            "id": char_id,
        }
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=photo,
            caption=_char_caption(char, update.effective_user.id, update.effective_user.first_name),
            parse_mode=ParseMode.HTML,
        )
        # Store the channel-post photo file_id for Inline Harem and Mini App proxying.
        char["message_id"] = msg.message_id
        # Save the photo file_id produced by the channel photo post.
        # This also normalizes images originally received as image documents.
        if msg.photo:
            char["img_url"] = msg.photo[-1].file_id
        await collection.insert_one(char)
        await update.message.reply_text(
            f"✅ <b>{parsed['name']}</b> uploaded!\n"
            f"🎴 Rarity: {parsed['rarity']}\n"
            f"📺 Anime: {parsed['anime']}\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Channel post failed: {e}\nCharacter was <b>not</b> saved.",
            parse_mode=ParseMode.HTML,
        )


# ── /delete ───────────────────────────────────────────────────────────────────

async def delete(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/delete ID</code>", parse_mode=ParseMode.HTML)
        return

    char = await collection.find_one_and_delete({"id": context.args[0]})
    if not char:
        await update.message.reply_text("❌ Character not found.")
        return
    if char.get("message_id"):
        try:
            await delete_now(context.bot, CHARA_CHANNEL_ID, char["message_id"], ignore_errors=True)
        except Exception:
            pass
    await update.message.reply_text(
        f"✅ Deleted <b>{char['name']}</b> (<code>{char['id']}</code>)",
        parse_mode=ParseMode.HTML,
    )


# ── /update ───────────────────────────────────────────────────────────────────

_VALID = {"img_url", "name", "anime", "rarity"}


async def update_char(upd: Update, context: CallbackContext) -> None:
    if not _is_sudo(upd.effective_user.id):
        await upd.message.reply_text("❌ Sudo only.")
        return
    replied = upd.message.reply_to_message
    raw = None

    if len(context.args) == 3:
        char_id, field, raw = context.args
        if field == "img_url":
            await upd.message.reply_text(
                "For image updates, reply directly to an image with "
                "<code>/update ID img_url</code> (no URL/value needed).",
                parse_mode=ParseMode.HTML,
            )
            return
    elif len(context.args) == 2 and context.args[1] == "img_url":
        char_id, field = context.args
        if not replied or not (
            getattr(replied, "photo", None)
            or (
                getattr(replied, "document", None)
                and (replied.document.mime_type or "").startswith("image/")
            )
        ):
            await upd.message.reply_text(
                "❌ Reply directly to a photo/image with "
                "<code>/update ID img_url</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
    else:
        await upd.message.reply_text(
            "Usage: <code>/update ID field new_value</code>\n"
            "Image: reply to an image with <code>/update ID img_url</code> (no URL needed)\n"
            "Fields: anime, name, rarity. For images, use the reply method above.",
            parse_mode=ParseMode.HTML,
        )
        return

    if field not in _VALID:
        await upd.message.reply_text(f"❌ Invalid field. Choose: {', '.join(sorted(_VALID))}")
        return

    char = await collection.find_one({"id": char_id})
    if not char:
        await upd.message.reply_text("❌ Character not found.")
        return

    if field in ("name", "anime"):
        new_val = raw.replace("-", " ").title()
    elif field == "rarity":
        try:
            new_val = RARITY_MAP[int(raw)]
        except (KeyError, ValueError):
            await upd.message.reply_text(f"❌ Invalid rarity. Choose from: {', '.join(map(str, RARITY_MAP))}.")
            return
    elif field == "img_url":
        if raw is None:
            if getattr(replied, "photo", None):
                source_file_id = replied.photo[-1].file_id
                filename, content_type = "character.jpg", "image/jpeg"
            else:
                source_file_id = replied.document.file_id
                filename = replied.document.file_name or "character-image"
                content_type = replied.document.mime_type or "image/jpeg"
            # Keep the Telegram file_id; the backend image proxy serves it to the Mini App.
            new_val = source_file_id
        else:
            await upd.message.reply_text(
                "For image updates, reply directly to an image with "
                "<code>/update ID img_url</code>. Image URLs are not accepted here.",
                parse_mode=ParseMode.HTML,
            )
            return
    else:
        new_val = raw

    await collection.update_one({"id": char_id}, {"$set": {field: new_val}})
    char[field] = new_val

    try:
        if field == "img_url":
            if char.get("message_id"):
                await delete_now(context.bot, CHARA_CHANNEL_ID, char["message_id"])
            msg = await context.bot.send_photo(
                CHARA_CHANNEL_ID, photo=new_val,
                caption=_char_caption(char, upd.effective_user.id, upd.effective_user.first_name),
                parse_mode=ParseMode.HTML,
            )
            # Keep the Telegram file_id in MongoDB; the Mini App uses the backend image proxy.
            # Store the actual photo ID returned by send_photo, not the
            # incoming ID (which could belong to a document upload).
            final_photo_id = msg.photo[-1].file_id if msg.photo else new_val
            char["img_url"] = final_photo_id
            await collection.update_one(
                {"id": char_id},
                {"$set": {"message_id": msg.message_id, "img_url": final_photo_id}},
            )
        elif char.get("message_id"):
            await context.bot.edit_message_caption(
                CHARA_CHANNEL_ID, char["message_id"],
                caption=_char_caption(char, upd.effective_user.id, upd.effective_user.first_name),
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        await upd.message.reply_text(f"⚠️ DB updated but channel sync failed: {e}")
        return

    await upd.message.reply_text(
        f"✅ <b>{char['name']}</b> — <code>{field}</code> updated.",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("upload", upload, block=False))
application.add_handler(CommandHandler("uploadchar", uploadchar, block=False))
application.add_handler(CommandHandler("delete", delete, block=False))
application.add_handler(CommandHandler("update", update_char, block=False))
