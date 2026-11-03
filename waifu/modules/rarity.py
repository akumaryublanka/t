"""
modules/rarity.py — /rarity command
Shows rarity counts, percentages, and estimated collection value.

Rarity numbers are continuous: 1–16
Main rarity numbers: 1–10
Event rarity numbers: 11–16
See waifu.config.Config.RARITY_MAP for the complete list.
"""

from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection
from waifu.config import Config


RARITY_MAP = Config.RARITY_MAP
RARITY_ORDER = list(RARITY_MAP)

# Estimated collection values in Grey Credit; event variants have separate values.
VALUE_MAP = {
    1: 100,
    2: 300,
    3: 600,
    4: 1000,
    5: 1500,
    6: 2500,
    7: 3500,
    8: 4000,
    9: 5000,
    10: 6500,
    11: 7000,
    12: 7000,
    13: 7500,
    14: 7000,
    15: 7500,
    16: 8000,
}

BAR_LENGTH = 12

# Backward compatibility for character records using the old rarity labels.
LEGACY_RARITY_MAP = {label: number for number, label in RARITY_MAP.items()}
# Preserve labels and numeric IDs from the previous event-rarity numbering.
LEGACY_RARITY_MAP.update({"🎄 Christmas": 13, "Christmas": 13})
LEGACY_RARITY_NUMBERS = {26: 11, 27: 12, 28: 13, 29: 14, 30: 15, 31: 16}


def _bar(value: int, total: int) -> str:
    if total == 0:
        return "░" * BAR_LENGTH
    filled = round(BAR_LENGTH * value / total)
    return "▓" * filled + "░" * (BAR_LENGTH - filled)


def _percent(value: int, total: int) -> str:
    if total == 0:
        return "0.0"
    return f"{100 * value / total:.1f}"


def _normalize_rarity(rarity) -> int:
    """Normalize numeric and string rarity values to a valid tier number."""
    if isinstance(rarity, int) and not isinstance(rarity, bool):
        return rarity if rarity in RARITY_MAP else LEGACY_RARITY_NUMBERS.get(rarity, 1)
    value = str(rarity).strip()
    if value.isdigit():
        numeric = int(value)
        return numeric if numeric in RARITY_MAP else LEGACY_RARITY_NUMBERS.get(numeric, 1)
    return LEGACY_RARITY_MAP.get(value, 1)


async def rarity_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    # Reply to another user to view their stats.
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        u_doc = await user_collection.find_one({"id": target.id})
        name = escape(target.first_name)
    else:
        u_doc = await user_collection.find_one({"id": user.id})
        name = escape(user.first_name)

    if not u_doc:
        await message.reply_text("❌ Pehle kuch characters collect karo!")
        return

    chars = u_doc.get("characters", [])
    if not chars:
        await message.reply_text("❌ Teri harem abhi khaali hai!")
        return

    # Count each character ID only once.
    unique_chars = list({c["id"]: c for c in chars if "id" in c}.values())
    total_unique = len(unique_chars)
    total_all = len(chars)

    rarity_count: dict[int, int] = {r: 0 for r in RARITY_ORDER}
    for char in unique_chars:
        rarity = _normalize_rarity(char.get("rarity", 1))
        rarity_count[rarity] += 1

    total_value = sum(
        VALUE_MAP[_normalize_rarity(char.get("rarity", 1))]
        for char in unique_chars
    )

    lines = []
    for rarity_num in RARITY_ORDER:
        count = rarity_count[rarity_num]
        label = RARITY_MAP[rarity_num]
        bar = _bar(count, total_unique)
        pct = _percent(count, total_unique)
        value = VALUE_MAP[rarity_num] * count
        lines.append(
            f"{label}\n"
            f"  [{bar}] <b>{count}</b> chars ({pct}%)\n"
            f"  ₲ {value:,} Grey Credit"
        )

    rarity_text = "\n\n".join(lines)

    # Highest tier first.
    rarest_line = ""
    for rarity_num in reversed(RARITY_ORDER):
        match = next(
            (
                char for char in unique_chars
                if _normalize_rarity(char.get("rarity", 1)) == rarity_num
            ),
            None,
        )
        if match:
            rarest_name = escape(str(match.get("name", "Unknown")))
            rarest_line = (
                f"\n🏆 <b>Rarest:</b> {rarest_name} "
                f"({RARITY_MAP[rarity_num]})"
            )
            break

    text = (
        f"📊 <b>{name}'s Rarity Stats</b>\n"
        f"{'─' * 28}\n"
        f"🗂 <b>{total_unique}</b> unique  ({total_all} total)\n"
        f"💎 Est. Value: <b>{total_value:,}</b> ₲ Grey Credit"
        f"{rarest_line}\n"
        f"{'─' * 28}\n\n"
        f"{rarity_text}"
    )

    await message.reply_text(text, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("rarity", rarity_command, block=False))
