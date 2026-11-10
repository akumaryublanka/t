"""NekoVerse Gacha — single and ten-pull character summons with pity."""
import random
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection, db
from waifu.config import Config

# Same canonical rarity odds as normal drops and /hclaim.
_RARITY_WEIGHTS = Config.RARITY_WEIGHT_MAP
_PITY_LIMIT = 10


def _rarity_number(value) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in Config.RARITY_MAP else Config.RARITY_LEGACY_NUMBER_MAP.get(value, 1)
    text = str(value or "").strip()
    if text.isdigit():
        number = int(text)
        number = number if number in Config.RARITY_MAP else Config.RARITY_LEGACY_NUMBER_MAP.get(number, 1)
        return number
    for number, label in Config.RARITY_MAP.items():
        if text == label:
            return number
    canonical = Config.RARITY_LEGACY_MAP.get(text)
    if canonical:
        return next((number for number, label in Config.RARITY_MAP.items() if label == canonical), 1)
    return 1


def _weighted_character(chars: list[dict], minimum_rarity: int = 1) -> dict:
    pool = [c for c in chars if _rarity_number(c.get("rarity")) >= minimum_rarity]
    if not pool:
        pool = chars

    # Choose a rarity tier first, so a large catalogue cannot make a tier
    # more common merely because it contains more characters.
    by_rarity: dict[int, list[dict]] = {}
    for char in pool:
        number = _rarity_number(char.get("rarity"))
        by_rarity.setdefault(number, []).append(char)
    available = [number for number in by_rarity if _RARITY_WEIGHTS.get(number, 0) > 0]
    if not available:
        return random.choice(pool)
    selected = random.choices(
        available,
        weights=[_RARITY_WEIGHTS[number] for number in available],
        k=1,
    )[0]
    return random.choice(by_rarity[selected])


async def _summon_settings() -> tuple[int, int, bool]:
    game_doc = await db["miniapp_settings"].find_one({"_id": "game"})
    feature_doc = await db["miniapp_settings"].find_one({"_id": "features"})
    game = (game_doc or {}).get("data") or {}
    features = (feature_doc or {}).get("data") or {}
    try:
        single = max(1, min(1000000, int(game.get("gacha_single_cost", 250))))
    except (TypeError, ValueError):
        single = 250
    try:
        ten = max(1, min(10000000, int(game.get("gacha_ten_cost", 2250))))
    except (TypeError, ValueError):
        ten = 2250
    enabled = game.get("gacha_enabled", True) is not False and features.get("gacha", True) is not False
    return single, ten, enabled


async def summon(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    single_cost, ten_cost, enabled = await _summon_settings()
    if not enabled:
        await message.reply_text("🎴 The summon portal is temporarily closed.")
        return

    count = 1
    if context.args:
        if context.args[0] not in {"1", "10"}:
            await message.reply_text(
                "Usage: <code>/summon</code> or <code>/summon 10</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        count = int(context.args[0])

    cost = single_cost if count == 1 else ten_cost
    await user_collection.update_one(
        {"id": user.id},
        {"$setOnInsert": {
            "id": user.id, "username": user.username,
            "first_name": user.first_name, "characters": [],
            "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0,
            "favorites": [],
        }},
        upsert=True,
    )
    player = await user_collection.find_one({"id": user.id}) or {}
    balance = int(player.get("coins", 0) or 0)
    if balance < cost:
        await message.reply_text(
            f"❌ You need <b>{cost:,} Neko Coins</b>, but have <b>{balance:,}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    characters = await collection.find({}).to_list(length=10000)
    if not characters:
        await message.reply_text("The character catalogue is empty right now.")
        return

    # Debit atomically so two simultaneous summon requests cannot overspend.
    debit = await user_collection.update_one(
        {"id": user.id, "coins": {"$gte": cost}},
        {"$inc": {"coins": -cost}},
    )
    if not debit.modified_count:
        latest = await user_collection.find_one({"id": user.id}) or {}
        await message.reply_text(
            f"❌ Your balance changed. Current balance: {int(latest.get('coins', 0) or 0):,} coins."
        )
        return

    pity = max(0, min(_PITY_LIMIT - 1, int(player.get("gacha_pity", 0) or 0)))
    old_ids = {str(c.get("id")) for c in player.get("characters", [])}
    pulls: list[dict] = []
    try:
        for _ in range(count):
            # The 10th consecutive non-Prime pull is guaranteed Prime or higher.
            picked = _weighted_character(characters, minimum_rarity=3 if pity >= _PITY_LIMIT - 1 else 1)
            char = {key: value for key, value in picked.items() if key != "_id"}
            char["id"] = str(char.get("id", ""))
            char["rarity"] = Config.RARITY_MAP[_rarity_number(char.get("rarity", 1))]
            pulls.append(char)
            if _rarity_number(char.get("rarity")) >= 3:
                pity = 0
            else:
                pity += 1
        await user_collection.update_one(
            {"id": user.id},
            {
                "$push": {"characters": {"$each": pulls}},
                "$set": {"gacha_pity": pity},
                "$inc": {"total_summons": count},
            },
        )
    except Exception:
        await user_collection.update_one({"id": user.id}, {"$inc": {"coins": cost}})
        raise

    lines = []
    best = max(pulls, key=lambda c: Config.RARITY_VALUE_MAP[_rarity_number(c.get("rarity", 1))])
    for index, char in enumerate(pulls, 1):
        rarity = Config.RARITY_MAP[_rarity_number(char.get("rarity", 1))]
        duplicate = "  ↳ <i>Duplicate</i>" if str(char.get("id")) in old_ids else ""
        lines.append(
            f"{index}. {escape(str(rarity))} <b>{escape(str(char.get('name', 'Unknown')))}</b>"
            f" — <i>{escape(str(char.get('anime', 'Unknown')))}</i>{duplicate}"
        )

    new_balance = balance - cost
    heading = "🌸 <b>NEKOVERSE SUMMON</b>" if count == 1 else "🌸 <b>10× NEKOVERSE SUMMON</b>"
    text = (
        f"{heading}\n━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines)
        + f"\n━━━━━━━━━━━━━━━━━━\n"
        f"🎴 Best pull: <b>{escape(str(best.get('name', 'Unknown')))}</b>\n"
        f"🛡️ Prime pity: <b>{pity}/{_PITY_LIMIT}</b>\n"
        f"🪙 Cost: <b>{cost:,}</b>  ·  Balance: <b>{new_balance:,}</b>"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("💗 View My Harem", switch_inline_query_current_chat=f"collection.{user.id}")
    ]])
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


application.add_handler(CommandHandler(["summon", "gacha"], summon, block=False))
