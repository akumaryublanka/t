"""
modules/quiz.py — Stable Quiz with rounds system
- Bot decides rounds (3-7)
- who_is and which_anime only
- Proper state cleanup
- 1 minute auto delete
"""
import asyncio
import random
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, collection, user_collection, db
from waifu.modules.auto_delete import schedule_delete, QUIZ_DELAY

_ALLOWED_GROUP         = -1004385116091
quiz_scores_collection = db["quiz_scores"]

DIFFICULTY = {
    "easy":   {"timeout": 40, "coins": 80,  "xp": 15, "options": 4, "emoji": "🟢"},
    "medium": {"timeout": 30, "coins": 150, "xp": 25, "options": 4, "emoji": "🟡"},
    "hard":   {"timeout": 20, "coins": 250, "xp": 40, "options": 4, "emoji": "🔴"},
}

# chat_id → quiz session
_sessions: dict[int, dict] = {}
_streaks:  dict[int, int]  = {}


def _streak_bonus(streak: int) -> int:
    if streak >= 10: return 200
    if streak >= 7:  return 150
    if streak >= 5:  return 100
    if streak >= 3:  return 50
    return 0

def _streak_emoji(streak: int) -> str:
    if streak >= 10: return "🔥🔥🔥"
    if streak >= 7:  return "🔥🔥"
    if streak >= 5:  return "🔥"
    if streak >= 3:  return "⚡"
    return ""

def _timer_bar(remaining: int, total: int) -> str:
    filled = max(0, round((remaining / total) * 10))
    color  = "🟩" if filled >= 7 else ("🟨" if filled >= 4 else "🟥")
    return color * filled + "⬛" * (10 - filled)


def _build_question(all_chars: list, difficulty: str) -> dict:
    cfg     = DIFFICULTY[difficulty]
    correct = random.choice(all_chars)
    q_type  = random.choice(["who_is", "which_anime"])

    if q_type == "which_anime":
        other_animes = list({c["anime"] for c in all_chars if c["anime"] != correct["anime"]})
        if len(other_animes) >= cfg["options"] - 1:
            wrong_animes = random.sample(other_animes, cfg["options"] - 1)
            choices = [correct["anime"]] + wrong_animes
            random.shuffle(choices)
            correct_idx = choices.index(correct["anime"])
            return {
                "type":        "which_anime",
                "correct":     correct,
                "question":    "📺 <b>Which anime is this character from?</b>",
                "choices":     choices,
                "correct_idx": correct_idx,
            }

    # who_is (default + fallback)
    pool    = [c for c in all_chars if c["id"] != correct["id"]]
    wrong   = random.sample(pool, min(cfg["options"] - 1, len(pool)))
    choices = [correct["name"]] + [c["name"] for c in wrong]
    random.shuffle(choices)
    correct_idx = choices.index(correct["name"])
    return {
        "type":        "who_is",
        "correct":     correct,
        "question":    "🎯 <b>Who is this character?</b>",
        "choices":     choices,
        "correct_idx": correct_idx,
    }


def _make_keyboard(chat_id: int, choices: list, round_num: int, difficulty: str) -> InlineKeyboardMarkup:
    """Use round number in callback to prevent stale button presses."""
    buttons = []
    for i, label in enumerate(choices):
        cb = f"qz|{chat_id}|{i}|{round_num}|{difficulty}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])
    return InlineKeyboardMarkup(buttons)


async def _send_round(bot, chat_id: int, session: dict) -> None:
    """Send current round question."""
    difficulty = session["difficulty"]
    cfg        = session.get("difficulty_config", DIFFICULTY[difficulty])
    q          = session["question"]
    round_num  = session["round"]
    total      = session["total_rounds"]
    timeout    = cfg["timeout"]

    kb      = _make_keyboard(chat_id, q["choices"], round_num, difficulty)
    bar     = _timer_bar(timeout, timeout)
    caption = (
        f"{cfg['emoji']} <b>{difficulty.upper()} QUIZ</b>  "
        f"Round <b>{round_num}/{total}</b>\n\n"
        f"{q['question']}\n\n"
        f"⏰ {bar} {timeout}s\n"
        f"₲ <b>{cfg['coins']} Grey Credits + {cfg['xp']} XP</b>"
    )

    try:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=q["correct"]["img_url"],
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        session["message_id"] = msg.message_id
        session["start_time"] = time.time()
        session["answered"]   = False

        # Store kb for timer updates
        session["kb"] = kb

        # Auto delete after QUIZ_DELAY
        schedule_delete(bot, chat_id, msg.message_id, QUIZ_DELAY)

        # Start timer + expire tasks
        asyncio.create_task(_round_timer(bot, chat_id, msg.message_id, kb, timeout, session["start_time"], round_num, difficulty, q))
        asyncio.create_task(_round_expire(bot, chat_id, msg.message_id, timeout, round_num))

    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"❌ Quiz error: {e}")
        _sessions.pop(chat_id, None)


async def _round_timer(bot, chat_id: int, message_id: int, kb: InlineKeyboardMarkup,
                       timeout: int, start_time: float, round_num: int,
                       difficulty: str, q: dict) -> None:
    """Update timer bar every 5 seconds."""
    session_at_start = _sessions.get(chat_id)
    cfg = (session_at_start or {}).get("difficulty_config", DIFFICULTY[difficulty])
    for _ in range(timeout // 5):
        await asyncio.sleep(5)

        session = _sessions.get(chat_id)
        # Stop if session gone, answered, or new round started
        if not session or session.get("answered") or session.get("round") != round_num:
            return

        elapsed   = time.time() - start_time
        remaining = max(0, timeout - int(elapsed))
        bar       = _timer_bar(remaining, timeout)

        new_caption = (
            f"{cfg['emoji']} <b>{difficulty.upper()} QUIZ</b>  "
            f"Round <b>{round_num}/{session['total_rounds']}</b>\n\n"
            f"{q['question']}\n\n"
            f"⏰ {bar} {remaining}s\n"
            f"₲ <b>{cfg['coins']} Grey Credits + {cfg['xp']} XP</b>"
        )
        try:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id,
                caption=new_caption, parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        except Exception:
            return


async def _round_expire(bot, chat_id: int, message_id: int, timeout: int, round_num: int) -> None:
    """Called when timer runs out."""
    await asyncio.sleep(timeout)

    session = _sessions.get(chat_id)
    if not session or session.get("answered") or session.get("round") != round_num:
        return  # Already answered or new round

    session["answered"] = True
    correct = session["question"]["correct"]

    try:
        await bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id,
            caption=(
                f"⏰ <b>Time's up!</b>\n\n"
                f"✅ Answer: <b>{escape(correct['name'])}</b>\n"
                f"📺 {escape(correct['anime'])}"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([]),
        )
    except Exception:
        pass

    # Move to next round after 2 seconds
    await asyncio.sleep(2)
    await _next_round(bot, chat_id)


async def _next_round(bot, chat_id: int) -> None:
    """Advance to next round or end quiz."""
    session = _sessions.get(chat_id)
    if not session:
        return

    next_round = session["round"] + 1

    if next_round > session["total_rounds"]:
        # Quiz over
        await _end_quiz(bot, chat_id, session)
        return

    # Build next question
    session["round"]    = next_round
    session["question"] = _build_question(session["all_chars"], session["difficulty"])
    session["answered"] = False

    await asyncio.sleep(1)
    await _send_round(bot, chat_id, session)


async def _end_quiz(bot, chat_id: int, session: dict) -> None:
    """Show final scoreboard."""
    _sessions.pop(chat_id, None)

    scores = session.get("scores", {})
    if scores:
        sorted_scores = sorted(scores.items(), key=lambda x: x[1]["correct"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (uid, data) in enumerate(sorted_scores):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(
                f"{medal} <a href='tg://user?id={uid}'>{escape(data['name'])}</a> "
                f"— {data['correct']} correct | ₲ {data['coins']:,} Grey Credits"
            )
        scoreboard = "\n".join(lines)
    else:
        scoreboard = "No one scored!"

    try:
        end_msg = await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🏁 <b>Quiz Over!</b>  ({session['total_rounds']} rounds)\n\n"
                f"🏆 <b>Scores:</b>\n{scoreboard}\n\n"
                f"<i>Play again with /quiz!</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(bot, chat_id, end_msg.message_id, QUIZ_DELAY)
    except Exception:
        pass


# ── COMMANDS ──────────────────────────────────────────────────────────────────

async def quiz(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if chat_id != _ALLOWED_GROUP:
        return

    settings_doc = await db["miniapp_settings"].find_one({"_id": "game"})
    features_doc = await db["miniapp_settings"].find_one({"_id": "features"})
    game_settings = (settings_doc or {}).get("data") or {}
    feature_settings = (features_doc or {}).get("data") or {}
    if game_settings.get("quiz_enabled", True) is False or feature_settings.get("quiz", True) is False:
        await update.message.reply_text("⏸️ Anime quiz is temporarily disabled by the bot owner.")
        return

    if chat_id in _sessions:
        await update.message.reply_text("⏳ A quiz is already running! Answer it first.")
        return

    # Parse difficulty
    difficulty = "medium"
    if context.args:
        arg = context.args[0].lower()
        if arg in DIFFICULTY:
            difficulty = arg
        else:
            await update.message.reply_text(
                "Usage: /quiz [easy|medium|hard]\n\n"
                "🟢 Easy — 40s | ₲ 80 Grey Credits\n"
                "🟡 Medium — 30s | ₲ 150 Grey Credits\n"
                "🔴 Hard — 20s | ₲ 250 Grey Credits",
            )
            return

    all_chars = await collection.find({}).to_list(length=5000)
    if len(all_chars) < 4:
        await update.message.reply_text("❌ Not enough characters in DB!")
        return

    # Snapshot owner-configured difficulty values for this full quiz session.
    cfg = dict(DIFFICULTY[difficulty])
    prefix = f"bot_quiz_{difficulty}_"
    try:
        cfg["timeout"] = max(5, min(300, int(game_settings.get(prefix + "timeout", cfg["timeout"]))))
        cfg["coins"] = max(0, min(100000000, int(game_settings.get(prefix + "coins", cfg["coins"]))))
        cfg["xp"] = max(0, min(1000000, int(game_settings.get(prefix + "xp", cfg["xp"]))))
    except (TypeError, ValueError):
        cfg = dict(DIFFICULTY[difficulty])

    # Bot decides rounds 3-7
    total_rounds = random.randint(3, 7)

    session = {
        "difficulty":   difficulty,
        "difficulty_config": cfg,
        "total_rounds": total_rounds,
        "round":        1,
        "all_chars":    all_chars,
        "question":     _build_question(all_chars, difficulty),
        "answered":     False,
        "message_id":   None,
        "start_time":   None,
        "kb":           None,
        "scores":       {},
    }
    _sessions[chat_id] = session

    await _send_round(context.bot, chat_id, session)


async def quiz_answer(update: Update, context: CallbackContext) -> None:
    q = update.callback_query

    parts = q.data.split("|")
    if len(parts) != 5:
        await q.answer("Invalid data.", show_alert=True)
        return

    try:
        chat_id       = int(parts[1])
        chosen_idx    = int(parts[2])
        cb_round      = int(parts[3])
        difficulty    = parts[4]
    except Exception:
        await q.answer("Invalid data.", show_alert=True)
        return

    if chat_id != _ALLOWED_GROUP:
        await q.answer()
        return

    session = _sessions.get(chat_id)

    # Stale button — round already moved on
    if not session or session.get("round") != cb_round:
        await q.answer("⏰ This round already ended!", show_alert=True)
        return

    if session.get("answered"):
        await q.answer("⏰ Already answered!", show_alert=True)
        return

    # Lock immediately
    session["answered"] = True

    await q.answer()

    user        = q.from_user
    correct_obj = session["question"]["correct"]
    correct_idx = session["question"]["correct_idx"]
    cfg         = session.get("difficulty_config", DIFFICULTY[difficulty])
    is_correct  = chosen_idx == correct_idx

    if is_correct:
        _streaks[user.id] = _streaks.get(user.id, 0) + 1
        streak      = _streaks[user.id]
        bonus       = _streak_bonus(streak)
        total_coins = cfg["coins"] + bonus
        streak_txt  = (
            f"\n🔥 Streak: <b>{streak}</b> {_streak_emoji(streak)}  +{bonus} bonus!"
            if bonus else f"\n🔥 Streak: <b>{streak}</b>"
        )

        # Update scores
        uid = user.id
        if uid not in session["scores"]:
            session["scores"][uid] = {"name": user.first_name, "correct": 0, "coins": 0}
        session["scores"][uid]["correct"] += 1
        session["scores"][uid]["coins"]   += total_coins

        await user_collection.update_one(
            {"id": uid},
            {
                "$inc": {"coins": total_coins, "xp": cfg["xp"]},
                "$set": {"username": user.username, "first_name": user.first_name},
                "$setOnInsert": {"characters": [], "favorites": [], "wins": 0},
            },
            upsert=True,
        )
        await quiz_scores_collection.update_one(
            {"user_id": uid},
            {
                "$inc": {"correct": 1, "coins_earned": total_coins},
                "$set": {"name": user.first_name},
                "$max": {"best_streak": streak},
            },
            upsert=True,
        )

        caption = (
            f"✅ <b>Correct!</b> {_streak_emoji(streak)}\n\n"
            f"🎉 <a href='tg://user?id={uid}'>{escape(user.first_name)}</a> got it!\n\n"
            f"🌸 <b>{escape(correct_obj['name'])}</b>\n"
            f"📺 {escape(correct_obj['anime'])}\n\n"
            f"₲ +{cfg['coins']} Grey Credits  ✨ +{cfg['xp']} XP"
            f"{streak_txt}"
        )
    else:
        _streaks[user.id] = 0
        await quiz_scores_collection.update_one(
            {"user_id": user.id},
            {"$inc": {"wrong": 1}, "$set": {"name": user.first_name}},
            upsert=True,
        )
        caption = (
            f"❌ <b>Wrong!</b>\n\n"
            f"<a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> "
            f"answered incorrectly!\n\n"
            f"✅ Answer: <b>{escape(correct_obj['name'])}</b>\n"
            f"📺 {escape(correct_obj['anime'])}\n\n"
            f"💔 Streak reset!"
        )

    try:
        await q.edit_message_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([]),
        )
    except Exception:
        pass

    # Next round after 2 seconds
    await asyncio.sleep(2)
    await _next_round(context.bot, chat_id)


async def quiz_leaderboard(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if chat_id != _ALLOWED_GROUP:
        return

    top = await quiz_scores_collection.find({}).sort("correct", -1).limit(10).to_list(10)
    if not top:
        await update.message.reply_text("📊 No quiz scores yet!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🏆 <b>Quiz Leaderboard</b>\n"]
    for i, doc in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f"{medal} <b>{escape(doc.get('name', 'Unknown'))}</b> — "
            f"✅ {doc.get('correct', 0)} correct | "
            f"₲ {doc.get('coins_earned', 0):,} Grey Credits | "
            f"🔥 Best streak: {doc.get('best_streak', 0)}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("quiz", quiz, block=False))
application.add_handler(CommandHandler("qlb",  quiz_leaderboard, block=False))
application.add_handler(CallbackQueryHandler(
    quiz_answer,
    pattern=r"^qz\|",
    block=False,
))
    
