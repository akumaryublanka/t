"""
modules/nguess.py — All character images tracked and deleted after 2 min
"""
import asyncio
import random
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import application, collection, user_collection, db
from waifu.modules.auto_delete import schedule_delete, NGUESS_DELAY
from waifu.config import Config

_COINS_REWARD  = 50
_XP_REWARD     = 25
_ALLOWED_GROUP = -1003852394368
_active_games: dict[int, dict] = {}


async def _send_character(chat_id: int, bot, game: dict) -> None:
    idx   = game["current_index"]
    char  = game["chars"][idx]
    total = game["total_rounds"]

    try:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=char["img_url"],
            caption=(
                f"🎯 <b>Round {idx + 1}/{total}</b>\n\n"
                f"❓ <b>Who is this character?</b>\n\n"
                f"🟡 <b>{game['coins_reward']} Neko Coins + {game['xp_reward']} XP</b>\n\n"
                f"<i>Type the character name to guess!</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
        game["message_id"] = msg.message_id
        # Track ALL image message ids
        game["all_msg_ids"].append(msg.message_id)
        # Each image scheduled for delete after 2 min
        schedule_delete(bot, chat_id, msg.message_id, NGUESS_DELAY)
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")


async def nguess(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if chat_id != _ALLOWED_GROUP:
        return

    settings_doc = await db["miniapp_settings"].find_one({"_id": "game"})
    features_doc = await db["miniapp_settings"].find_one({"_id": "features"})
    game_settings = (settings_doc or {}).get("data") or {}
    feature_settings = (features_doc or {}).get("data") or {}
    if feature_settings.get("nguess", True) is False:
        await update.message.reply_text("⏸️ NGuess is temporarily disabled by the bot owner.")
        return
    try:
        coins_reward = max(0, min(100000000, int(game_settings.get("nguess_coins_reward", _COINS_REWARD))))
        xp_reward = max(0, min(1000000, int(game_settings.get("nguess_xp_reward", _XP_REWARD))))
    except (TypeError, ValueError):
        coins_reward, xp_reward = _COINS_REWARD, _XP_REWARD

    if chat_id in _active_games:
        game  = _active_games[chat_id]
        idx   = game["current_index"]
        total = game["total_rounds"]
        await update.message.reply_text(
            f"⏳ <b>Game already running!</b>\nRound <b>{idx + 1}/{total}</b> in progress.",
            parse_mode=ParseMode.HTML,
        )
        return

    all_chars = await collection.find({}).to_list(length=5000)
    if len(all_chars) < 3:
        await update.message.reply_text("❌ Not enough characters in DB!")
        return

    total_rounds = random.randint(3, 8)
    chars        = random.sample(all_chars, total_rounds)

    game = {
        "chars":         chars,
        "current_index": 0,
        "total_rounds":  total_rounds,
        "message_id":    None,
        "all_msg_ids":   [],    # Track every image sent
        "scores":        {},
        "coins_reward":  coins_reward,
        "xp_reward":     xp_reward,
    }
    _active_games[chat_id] = game

    start_msg = await update.message.reply_text(
        f"🎮 <b>NGuess Game Started!</b>\n\n"
        f"📋 <b>Rules:</b>\n"
        f"┣ Type character name to guess\n"
        f"┣ Correct → next character\n"
        f"┗ Bot decides when to stop!\n\n"
        f"🟡 <b>{coins_reward} Neko Coins + {xp_reward} XP</b> per correct\n\n"
        f"<i>Get ready...</i> 🎯",
        parse_mode=ParseMode.HTML,
    )
    schedule_delete(context.bot, chat_id, start_msg.message_id, NGUESS_DELAY)

    await _send_character(chat_id, context.bot, game)


async def nguess_message(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if chat_id != _ALLOWED_GROUP:
        return

    game = _active_games.get(chat_id)
    if not game:
        return

    user       = update.effective_user
    user_guess = update.message.text.strip().lower()

    if len(user_guess) < 3:
        return

    idx  = game["current_index"]
    char = game["chars"][idx]

    name_parts = char["name"].lower().split()
    correct = (
        sorted(name_parts) == sorted(user_guess.split())
        or any(part == user_guess for part in name_parts)
    )

    if not correct:
        return

    # ── Correct ───────────────────────────────────────────────────────
    uid = user.id
    if uid not in game["scores"]:
        game["scores"][uid] = {"name": user.first_name, "coins": 0, "correct": 0}
    game["scores"][uid]["coins"]   += game["coins_reward"]
    game["scores"][uid]["correct"] += 1

    await user_collection.update_one(
        {"id": uid},
        {
            "$inc": {"coins": game["coins_reward"], "xp": game["xp_reward"]},
            "$set": {"username": user.username, "first_name": user.first_name},
            "$setOnInsert": {"characters": [], "favorites": [], "wins": 0, "total_guesses": 0},
        },
        upsert=True,
    )

    correct_msg = await update.message.reply_text(
        f"✅ <b>Correct!</b> "
        f"<a href='tg://user?id={uid}'>{escape(user.first_name)}</a> got it!\n\n"
        f"🌸 <b>{escape(char['name'])}</b>\n"
        f"📺 {escape(char['anime'])}\n"
        f"💎 {Config.RARITY_LEGACY_MAP.get(str(char.get('rarity', '')), char.get('rarity', Config.RARITY_MAP[1]))}\n\n"
        f"🟡 +{game['coins_reward']} Neko Coins  ✨ +{game['xp_reward']} XP",
        parse_mode=ParseMode.HTML,
    )
    schedule_delete(context.bot, chat_id, correct_msg.message_id, NGUESS_DELAY)

    game["current_index"] += 1

    if game["current_index"] >= game["total_rounds"]:
        _active_games.pop(chat_id, None)

        scores = game["scores"]
        if scores:
            sorted_scores = sorted(scores.items(), key=lambda x: x[1]["correct"], reverse=True)
            medals = ["🥇", "🥈", "🥉"]
            lines  = []
            for i, (uid2, data) in enumerate(sorted_scores):
                medal = medals[i] if i < 3 else f"{i+1}."
                lines.append(
                    f"{medal} <a href='tg://user?id={uid2}'>{escape(data['name'])}</a> "
                    f"— {data['correct']} correct  |  🟡 {data['coins']:,} Neko Coins"
                )
            scoreboard = "\n".join(lines)
        else:
            scoreboard = "No one scored!"

        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎮 <b>Game Over!</b>\n\n"
                f"🏆 <b>Final Scores:</b>\n{scoreboard}\n\n"
                f"<i>Play again with /nguess!</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(context.bot, chat_id, end_msg.message_id, NGUESS_DELAY)
        return

    await asyncio.sleep(2)
    await _send_character(chat_id, context.bot, game)


async def nguess_stop(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if chat_id != _ALLOWED_GROUP:
        return

    if chat_id not in _active_games:
        await update.message.reply_text("❌ No active NGuess game!")
        return

    game  = _active_games.pop(chat_id)
    idx   = game["current_index"]
    total = game["total_rounds"]

    # Delete all remaining images immediately
    for msg_id in game.get("all_msg_ids", []):
        schedule_delete(context.bot, chat_id, msg_id, 0)

    stop_msg = await update.message.reply_text(
        f"🛑 <b>Game stopped!</b>\nCompleted <b>{idx}/{total}</b> rounds.\n\n"
        f"<i>Start again with /nguess</i>",
        parse_mode=ParseMode.HTML,
    )
    schedule_delete(context.bot, chat_id, stop_msg.message_id, NGUESS_DELAY)


application.add_handler(CommandHandler("nguess",      nguess,      block=False))
application.add_handler(CommandHandler("nguess_stop", nguess_stop, block=False))
application.add_handler(MessageHandler(
    filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
    nguess_message, block=False,
), group=1)
        
