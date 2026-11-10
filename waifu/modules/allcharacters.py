"""
modules/allcharacters.py — /allcharacters
Lists every character in the database, grouped by rarity, with per-rarity counts.
"""
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection
from waifu.config import Config


RARITY_MAP = Config.RARITY_MAP
LABEL_TO_RARITY = {label.casefold(): number for number, label in RARITY_MAP.items()}
LABEL_TO_RARITY.update({
    old_label.casefold(): number
    for old_label, canonical in Config.RARITY_LEGACY_MAP.items()
    for number, label in RARITY_MAP.items()
    if label == canonical
})
LEGACY_RARITY_NUMBERS = {26: 11, 27: 12, 28: 13, 29: 14, 30: 15, 31: 16}
LABEL_TO_RARITY.update({"🎄 christmas": 13, "christmas": 13})


def _rarity_number(value) -> int:
    if isinstance(value, int):
        return value if value in RARITY_MAP else LEGACY_RARITY_NUMBERS.get(value, 1)
    raw = str(value or "").strip()
    if raw.isdigit():
        number = int(raw)
        return number if number in RARITY_MAP else LEGACY_RARITY_NUMBERS.get(number, 1)
    normalized = raw.casefold()
    if normalized in LABEL_TO_RARITY:
        return LABEL_TO_RARITY[normalized]
    # Support active records containing just the rarity name without its emoji.
    for label, number in LABEL_TO_RARITY.items():
        if label.endswith(normalized) or normalized.endswith(label.split(" ", 1)[-1]):
            return number
    return 1


async def allcharacters(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    if not message:
        return

    cursor = collection.find({}, {"_id": 0, "id": 1, "name": 1, "anime": 1, "rarity": 1}).sort("id", 1)
    grouped: dict[int, list[dict]] = {number: [] for number in RARITY_MAP}
    total = 0

    async for char in cursor:
        grouped[_rarity_number(char.get("rarity"))].append(char)
        total += 1

    if total == 0:
        await message.reply_text("❌ Character database is empty.")
        return

    counts = [f"{RARITY_MAP[number]}: {len(chars)}" for number, chars in grouped.items() if chars]
    header = (
        "📚 <b>ALL CHARACTERS DATABASE</b>\n"
        f"🗂 Total characters: <b>{total}</b>\n"
        + "\n".join(counts)
    )
    await message.reply_text(header, parse_mode=ParseMode.HTML)

    # Send complete lists in safe-sized messages, grouped by rarity.
    for number, chars in grouped.items():
        if not chars:
            continue
        lines = [f"\n{RARITY_MAP[number]} — <b>{len(chars)} characters</b>"]
        for char in chars:
            name = escape(str(char.get("name") or "Unknown"))
            anime = escape(str(char.get("anime") or "Unknown"))
            char_id = escape(str(char.get("id") or "—"))
            line = f"\n• <b>{name}</b> — {anime} <code>#{char_id}</code>"
            if sum(len(part) for part in lines) + len(line) > 3600:
                await message.reply_text("".join(lines), parse_mode=ParseMode.HTML)
                lines = [f"{RARITY_MAP[number]} <i>(continued)</i>"]
            lines.append(line)
        if lines:
            await message.reply_text("".join(lines), parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("allcharacters", allcharacters, block=False))
application.add_handler(CommandHandler("charlist", allcharacters, block=False))
