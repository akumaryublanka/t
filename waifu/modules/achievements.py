"""Achievement cabinet — milestones are calculated from live player stats."""
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection
from waifu.config import Config


def _rarity_num(value) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in Config.RARITY_MAP else 1
    text = str(value or "").strip()
    if text.isdigit():
        return int(text) if int(text) in Config.RARITY_MAP else 1
    for number, label in Config.RARITY_MAP.items():
        if label == text:
            return number
    return 1


def _progress(value: int, target: int) -> str:
    pct = min(100, int(value * 100 / max(target, 1)))
    filled = round(pct / 10)
    return "🟪" * filled + "⬛" * (10 - filled)


async def achievements(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    target = user
    player = None
    if update.effective_message.reply_to_message:
        target = update.effective_message.reply_to_message.from_user
        player = await user_collection.find_one({"id": target.id})
    else:
        player = await user_collection.find_one({"id": user.id})
    if not player:
        await update.effective_message.reply_text("Start collecting characters to unlock your first achievement!")
        return

    chars = player.get("characters", [])
    unique = list({str(c.get("id")): c for c in chars}.values())
    rarity_counts = {}
    for char in unique:
        tier = _rarity_num(char.get("rarity"))
        rarity_counts[tier] = rarity_counts.get(tier, 0) + 1

    wins = int(player.get("wins", 0) or 0)
    xp = int(player.get("xp", 0) or 0)
    streak = int(player.get("daily_streak", 0) or 0)
    tower = int(player.get("tower_best_floor", 0) or 0)
    scramble = int(player.get("scramble_wins", 0) or 0)
    unique_count = len(unique)

    milestones = [
        ("🌱 First Catch", "Collect your first character", unique_count, 1),
        ("🎴 Growing Collection", "Collect 10 unique characters", unique_count, 10),
        ("📚 Anime Archivist", "Collect 50 unique characters", unique_count, 50),
        ("👑 Grand Collector", "Collect 100 unique characters", unique_count, 100),
        ("💜 Rare Hunter", "Own 5 Rare-or-higher characters", sum(v for k, v in rarity_counts.items() if k >= 3), 5),
        ("🌟 Legendary Soul", "Own 3 Legendary-or-higher characters", sum(v for k, v in rarity_counts.items() if k >= 5), 3),
        ("⚔️ Arena Victor", "Win 5 character duels", wins, 5),
        ("🔥 Daily Devotion", "Reach a 7-day daily streak", streak, 7),
        ("🏯 Tower Climber", "Reach floor 10 in the Infinite Tower", tower, 10),
        ("🧩 Word Wizard", "Win 10 scramble rounds", scramble, 10),
        ("✨ XP Apprentice", "Earn 1,000 XP", xp, 1000),
    ]

    unlocked = sum(value >= goal for _, _, value, goal in milestones)
    lines = [
        f"🏅 <b>{escape(player.get('first_name', target.first_name))}'S ACHIEVEMENTS</b>",
        f"🔓 Unlocked: <b>{unlocked}/{len(milestones)}</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for title, description, value, goal in milestones:
        done = value >= goal
        status = "✅" if done else "🔒"
        value_text = f"{min(value, goal):,}/{goal:,}"
        lines.append(
            f"\n{status} <b>{title}</b>\n"
            f"<i>{description}</i>\n"
            f"{_progress(value, goal)}  {value_text}"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("achievements", achievements, block=False))
