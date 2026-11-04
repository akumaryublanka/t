"""
modules/redeem.py

Owner commands:
/gencode coins <amount> <uses>      — Generate a Neko Coin redeem code
/gencode char <character_id> <uses> — Generate a character redeem code

User command:
/redeem <code> — Redeem a code
"""
import random
import string
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection, db, OWNER_ID

redeem_collection = db["redeem_codes"]


def _gen_code(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


async def _starting_coins() -> int:
    settings_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
    settings = (settings_doc or {}).get("data") or {}
    try:
        return max(0, min(100000000, int(settings.get("starting_coins", 0))))
    except (TypeError, ValueError):
        return 0


# ── /gencode ──────────────────────────────────────────────────────────────────

async def gencode(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage:\n"
            "<code>/gencode coins &lt;amount&gt; &lt;uses&gt;</code>\n"
            "<code>/gencode char &lt;char_id&gt; &lt;uses&gt;</code>\n\n"
            "Example:\n"
            "<code>/gencode coins 500 10</code>\n"
            "<code>/gencode char 0001 5</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code_type = args[0].lower()
    uses      = args[2]

    if not uses.isdigit() or int(uses) < 1:
        await update.message.reply_text("❌ Uses must be a positive number!")
        return

    uses = int(uses)
    code = _gen_code()

    if code_type == "coins":
        amount = args[1]
        if not amount.isdigit() or int(amount) < 1:
            await update.message.reply_text("❌ Amount must be a positive number!")
            return
        amount = int(amount)

        await redeem_collection.insert_one({
            "code":       code,
            "type":       "coins",
            "amount":     amount,
            "uses_left":  uses,
            "total_uses": uses,
            "used_by":    [],
        })

        await update.message.reply_text(
            f"✅ <b>Neko Coin Code Generated!</b>\n\n"
            f"🎟 Code: <code>{code}</code>\n"
            f"🟡 Neko Coins: <b>{amount:,}</b>\n"
            f"🔁 Uses: <b>{uses}</b>\n\n"
            f"Share this code with users!",
            parse_mode=ParseMode.HTML,
        )

    elif code_type == "char":
        char_id = args[1]
        char    = await collection.find_one({"id": char_id})
        if not char:
            await update.message.reply_text(
                f"❌ No character found with ID <code>{escape(char_id)}</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        await redeem_collection.insert_one({
            "code":       code,
            "type":       "char",
            "char_id":    char_id,
            "char_name":  char["name"],
            "char_rarity": char.get("rarity", "Unknown"),
            "uses_left":  uses,
            "total_uses": uses,
            "used_by":    [],
        })

        await update.message.reply_text(
            f"✅ <b>Character Code Generated!</b>\n\n"
            f"🎟 Code: <code>{code}</code>\n"
            f"🌸 Character: <b>{escape(char['name'])}</b>\n"
            f"💎 Rarity: {char.get('rarity', 'Unknown')}\n"
            f"🔁 Uses: <b>{uses}</b>\n\n"
            f"Share this code with users!",
            parse_mode=ParseMode.HTML,
        )

    else:
        await update.message.reply_text(
            "❌ Invalid type! Use <code>coins</code> or <code>char</code>.",
            parse_mode=ParseMode.HTML,
        )


# ── /redeem ───────────────────────────────────────────────────────────────────

async def redeem(update: Update, context: CallbackContext) -> None:
    user    = update.effective_user
    user_id = user.id

    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/redeem &lt;code&gt;</code>\n"
            "Example: <code>/redeem ABC123XYZ</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = context.args[0].strip().upper()
    doc  = await redeem_collection.find_one({"code": code})

    if not doc:
        await update.message.reply_text("❌ Invalid code! Check and try again.")
        return

    if doc["uses_left"] <= 0:
        await update.message.reply_text("❌ This code has expired!")
        return

    if user_id in doc.get("used_by", []):
        await update.message.reply_text("❌ You have already used this code!")
        return

    # Update uses
    await redeem_collection.update_one(
        {"code": code},
        {
            "$inc":  {"uses_left": -1},
            "$push": {"used_by": user_id},
        },
    )

    # Give reward
    if doc["type"] == "coins":
        amount = doc["amount"]
        # Seed new accounts first so the configured starting balance is
        # additive to the reward instead of being replaced by the redemption.
        existing_user = await user_collection.find_one({"id": user_id}, {"_id": 1})
        if not existing_user:
            starting_coins = await _starting_coins()
            await user_collection.update_one(
                {"id": user_id},
                {"$setOnInsert": {
                    "id": user_id, "username": user.username, "first_name": user.first_name,
                    "characters": [], "coins": starting_coins, "xp": 0, "wins": 0,
                    "total_guesses": 0, "favorites": [],
                }},
                upsert=True,
            )
        await user_collection.update_one(
            {"id": user_id},
            {
                "$inc": {"coins": amount},
                "$set": {"username": user.username, "first_name": user.first_name},
                "$setOnInsert": {
                    "xp": 0, "wins": 0,
                    "characters": [], "favorites": [],
                },
            },
            upsert=True,
        )
        await update.message.reply_text(
            f"🎉 <b>Code Redeemed!</b>\n\n"
            f"🟡 <b>+{amount:,} Neko Coins</b> added to your wallet!\n\n"
            f"🔁 Uses remaining: <b>{doc['uses_left'] - 1}</b>",
            parse_mode=ParseMode.HTML,
        )

    elif doc["type"] == "char":
        char = await collection.find_one({"id": doc["char_id"]})
        if not char:
            await update.message.reply_text("❌ Character no longer exists in DB.")
            return

        await user_collection.update_one(
            {"id": user_id},
            {
                "$push": {"characters": char},
                "$set":  {"username": user.username, "first_name": user.first_name},
                "$setOnInsert": {
                    "coins": await _starting_coins(), "xp": 0, "wins": 0, "favorites": [],
                },
            },
            upsert=True,
        )

        caption = (
            f"🎉 <b>Code Redeemed!</b>\n\n"
            f"🌸 <b>{escape(char['name'])}</b> added to your harem!\n"
            f"📺 Anime: {escape(char['anime'])}\n"
            f"💎 Rarity: {char.get('rarity', 'Unknown')}\n\n"
            f"🔁 Uses remaining: <b>{doc['uses_left'] - 1}</b>"
        )

        photo = char.get("img_url")
        if photo:
            try:
                await update.message.reply_photo(
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception:
                pass
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


# ── /codes — Owner check all active codes ─────────────────────────────────────

async def codes(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return

    all_codes = await redeem_collection.find({"uses_left": {"$gt": 0}}).to_list(length=100)

    if not all_codes:
        await update.message.reply_text("📭 No active codes right now.")
        return

    lines = ["🎟 <b>Active Redeem Codes</b>\n"]
    for c in all_codes:
        if c["type"] == "coins":
            lines.append(
                f"<code>{c['code']}</code> — 💰 {c['amount']:,} coins | 🔁 {c['uses_left']}/{c['total_uses']}"
            )
        else:
            lines.append(
                f"<code>{c['code']}</code> — 🌸 {escape(c['char_name'])} | 🔁 {c['uses_left']}/{c['total_uses']}"
            )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ── /delcode — Owner delete a code ────────────────────────────────────────────

async def delcode(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/delcode &lt;code&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = context.args[0].strip().upper()
    res  = await redeem_collection.delete_one({"code": code})

    if res.deleted_count:
        await update.message.reply_text(f"✅ Code <code>{code}</code> deleted!", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Code not found.")


application.add_handler(CommandHandler("gencode",  gencode,  block=False))
application.add_handler(CommandHandler("redeem",   redeem,   block=False))
application.add_handler(CommandHandler("codes",    codes,    block=False))
application.add_handler(CommandHandler("delcode",  delcode,  block=False))
  
