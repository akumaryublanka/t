"""
modules/hclaim.py — Daily Free Character Claim
Deletes claim message after 5 minutes.
"""
import random
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

from waifu import application, user_collection, collection, db
from waifu.modules.auto_delete import schedule_delete, HCLAIM_DELAY
from waifu.config import Config

ALLOWED_GROUP_ID   = -1004385116091
ALLOWED_GROUP_LINK = "https://t.me/KyaHiNaamRakhein"

hclaim_col = db["hclaim_cooldowns"]

RARITY_MAP = Config.RARITY_MAP

# Same rarity odds as normal drops. Event rarities are given the legacy Extreme-tier weight.
_RARITY_WEIGHT_BY_ID = {
    1: 1000,
    2: 500,
    3: 250,
    4: 100,
    5: 40,
    6: 15,
    7: 5,
    8: 2,
    9: 1,
    10: 0.5,
    11: 0.4,
    12: 0.35,
    13: 0.3,
    14: 0.25,
    15: 0.2,
    16: 0.15,
    17: 0.12,
    18: 0.1,
    19: 0.08,
    20: 0.06,
    21: 0.05,
    22: 0.04,
    23: 0.03,
    24: 0.02,
    25: 0.01,
    26: 0.5,
    27: 0.5,
    28: 0.5,
    29: 0.5,
    30: 0.5,
    31: 0.5,
}
RARITY_WEIGHTS = {
    RARITY_MAP[number]: weight
    for number, weight in _RARITY_WEIGHT_BY_ID.items()
    if number in RARITY_MAP
}

def _rarity_label(value) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return RARITY_MAP.get(value, "⚪ Common")
    if isinstance(value, str):
        if value in RARITY_WEIGHTS:
            return value
        # Accept numeric rarity strings stored by older uploads.
        if value.isdigit():
            return RARITY_MAP.get(int(value), "⚪ Common")
    return "⚪ Common"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _get_random_character() -> dict | None:
    """Choose a character using the same rarity-weighted odds as normal drops."""
    chars = await collection.find({}).to_list(length=5000)
    if not chars:
        return None
    weights = [RARITY_WEIGHTS[_rarity_label(c.get("rarity", 1))] for c in chars]
    return random.choices(chars, weights=weights, k=1)[0]


async def _get_cooldown(user_id: int) -> datetime | None:
    doc = await hclaim_col.find_one({"user_id": user_id})
    if doc and "last_claim" in doc:
        return doc["last_claim"].replace(tzinfo=timezone.utc)
    return None


async def _set_cooldown(user_id: int):
    await hclaim_col.update_one(
        {"user_id": user_id},
        {"$set": {"last_claim": _now_utc()}},
        upsert=True
    )


async def hclaim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat    = update.effective_chat
    user    = update.effective_user
    message = update.effective_message

    # ── Group check ───────────────────────────────────────────────────────
    if chat.id != ALLOWED_GROUP_ID:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌸 Join Group", url=ALLOWED_GROUP_LINK)
        ]])
        await message.reply_text(
            "❌ <b>/hclaim</b> is only available in a special group!\n\n"
            "👇 Join below to get a free character every day:",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        return

    # ── Cooldown check ────────────────────────────────────────────────────
    last_claim = await _get_cooldown(user.id)
    if last_claim:
        next_claim = last_claim + timedelta(hours=24)
        now        = _now_utc()
        if now < next_claim:
            remaining  = next_claim - now
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes    = rem // 60
            await message.reply_text(
                f"⏳ <b>{user.first_name}</b>, you already claimed today!\n\n"
                f"🕐 Come back in: <b>{hours}h {minutes}m</b>",
                parse_mode=ParseMode.HTML,
            )
            return

    # ── Get random character ──────────────────────────────────────────────
    char = await _get_random_character()
    if not char:
        await message.reply_text("⚠️ No characters available right now. Try again later!")
        return

    # ── Add to harem — same format as waifu_drop ─────────────────────────
    rarity_txt = _rarity_label(char.get("rarity", 1))

    economy_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
    economy_settings = (economy_doc or {}).get("data") or {}
    try:
        starting_coins = max(0, min(100000000, int(economy_settings.get("starting_coins", 0))))
    except (TypeError, ValueError):
        starting_coins = 0

    await user_collection.update_one(
        {"id": user.id},
        {
            "$push": {"characters": char},
            "$inc":  {"total_guesses": 1},
            "$set":  {"username": user.username, "first_name": user.first_name},
            "$setOnInsert": {
                "coins":     starting_coins,
                "wins":      0,
                "xp":        0,
                "favorites": [],
            },
        },
        upsert=True,
    )

    await _set_cooldown(user.id)

    # ── Success message ───────────────────────────────────────────────────
    caption = (
        f"🎴 <b>Daily Claim!</b>\n\n"
        f"🌸 <b>{char.get('name', 'Unknown')}</b>\n"
        f"📺 Anime: <i>{char.get('anime', 'Unknown')}</i>\n"
        f"✨ Rarity: {rarity_txt}\n\n"
        f"👤 Claimed by: <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
        f"⏰ Come back in 24 hours!\n\n"
        f"<i>🗑 This message will be deleted in 5 minutes.</i>"
    )

    img_url = char.get("img_url", "")
    try:
        if img_url:
            sent = await message.reply_photo(
                photo=img_url, caption=caption, parse_mode=ParseMode.HTML,
            )
        else:
            sent = await message.reply_text(caption, parse_mode=ParseMode.HTML)
    except Exception:
        sent = await message.reply_text(caption, parse_mode=ParseMode.HTML)

    schedule_delete(context.bot, chat.id, sent.message_id, HCLAIM_DELAY)


application.add_handler(CommandHandler("hclaim", hclaim_command))
                                 
