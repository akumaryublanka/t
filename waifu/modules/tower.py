"""
NekoVerse Infinite Tower — solo arithmetic floors with safe, skill-based rewards.
"""
import random
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection

_sessions: dict[int, dict] = {}
_LIVES = 3
_COOLDOWN_SECONDS = 300


def _make_problem(floor: int) -> tuple[str, int]:
    if floor <= 3:
        a, b = random.randint(8, 25), random.randint(3, 18)
        if random.choice((True, False)):
            return f"{a} + {b} = ?", a + b
        if a < b:
            a, b = b, a
        return f"{a} - {b} = ?", a - b

    if floor <= 6:
        a, b = random.randint(3, 12), random.randint(2, 9)
        return f"{a} × {b} = ?", a * b

    if floor <= 10:
        a, b, c = random.randint(3, 12), random.randint(2, 9), random.randint(5, 25)
        return f"({a} × {b}) + {c} = ?", (a * b) + c

    a, b = random.randint(5, 15), random.randint(3, 12)
    c, d = random.randint(10, 30), random.randint(1, 9)
    return f"({a} × {b}) + {c} - {d} = ?", (a * b) + c - d


def _make_choices(answer: int, floor: int) -> tuple[list[int], int]:
    choices = {answer}
    spread = min(35, 5 + floor * 2)
    while len(choices) < 4:
        choices.add(max(0, answer + random.randint(-spread, spread)))
    result = list(choices)
    random.shuffle(result)
    return result, result.index(answer)


def _keyboard(user_id: int, session: dict) -> InlineKeyboardMarkup:
    rows = []
    for index, value in enumerate(session["choices"]):
        rows.append([InlineKeyboardButton(
            str(value),
            callback_data=f"tower:{user_id}:{session['floor']}:{index}",
        )])
    return InlineKeyboardMarkup(rows)


def _floor_text(user_name: str, session: dict, note: str = "") -> str:
    return (
        "🏯 <b>NEKOVERSE — INFINITE TOWER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{escape(user_name)}</b>\n"
        f"🏁 Floor: <b>{session['floor']}</b>\n"
        f"❤️ Lives: {'❤️' * session['lives']}{'🖤' * (_LIVES - session['lives'])}\n"
        f"🟡 Coins earned: <b>{session['earned']:,}</b>\n\n"
        f"🧩 <b>Solve this challenge:</b>\n"
        f"<code>{session['question']}</code>\n\n"
        "Choose the correct answer below. Every correct floor earns Neko Coins and XP."
        + (f"\n\n{note}" if note else "")
    )


async def tower(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    if user.id in _sessions:
        await message.reply_text(
            "🏯 Your tower run is already active. Keep answering or use /tower_stop."
        )
        return

    existing = await user_collection.find_one({"id": user.id}, {"last_tower": 1})
    try:
        last_tower = float((existing or {}).get("last_tower", 0) or 0)
    except (TypeError, ValueError):
        last_tower = 0
    remaining = _COOLDOWN_SECONDS - (time.time() - last_tower)
    if remaining > 0:
        minutes, seconds = divmod(int(remaining), 60)
        await message.reply_text(
            f"⏳ Your next tower run is ready in <b>{minutes}m {seconds}s</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    session = {
        "floor": 1,
        "lives": _LIVES,
        "earned": 0,
        "solved": 0,
    }
    session["question"], answer = _make_problem(session["floor"])
    session["choices"], session["correct_index"] = _make_choices(answer, session["floor"])
    _sessions[user.id] = session

    await user_collection.update_one(
        {"id": user.id},
        {
            "$set": {
                "last_tower": time.time(),
                "username": user.username,
                "first_name": user.first_name,
            },
            "$setOnInsert": {
                "characters": [], "coins": 0, "xp": 0, "wins": 0,
                "total_guesses": 0, "favorites": [],
            },
        },
        upsert=True,
    )
    await message.reply_text(
        _floor_text(user.first_name, session),
        reply_markup=_keyboard(user.id, session),
        parse_mode=ParseMode.HTML,
    )


async def tower_stop(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    session = _sessions.pop(user.id, None) if user else None
    if not session:
        await update.effective_message.reply_text("You don't have an active tower run.")
        return
    await update.effective_message.reply_text(
        "🏁 <b>Tower run ended!</b>\n"
        f"Floors cleared: <b>{session['solved']}</b>\n"
        f"🟡 Neko Coins earned: <b>{session['earned']:,}</b>\n"
        "Your earned rewards are already in your balance.",
        parse_mode=ParseMode.HTML,
    )


async def tower_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user = query.from_user
    try:
        _, owner_text, floor_text, index_text = (query.data or "").split(":")
        owner_id, floor, index = int(owner_text), int(floor_text), int(index_text)
    except (ValueError, TypeError):
        await query.answer("This tower button is invalid.", show_alert=True)
        return

    session = _sessions.get(owner_id)
    if user.id != owner_id:
        await query.answer("This is someone else's tower run.", show_alert=True)
        return
    if not session or session["floor"] != floor:
        await query.answer("This floor has already moved on.", show_alert=True)
        return
    if index < 0 or index >= len(session["choices"]):
        await query.answer("Invalid answer.", show_alert=True)
        return

    correct = index == session["correct_index"]
    completed_floor = session["floor"]
    session["floor"] += 1

    if correct:
        reward = 15 + min(completed_floor * 3, 60)
        xp_reward = 5 + min(completed_floor, 10)
        session["earned"] += reward
        session["solved"] += 1
        await user_collection.update_one(
            {"id": owner_id},
            {
                "$inc": {
                    "coins": reward,
                    "xp": xp_reward,
                    "tower_floors": 1,
                },
                "$max": {"tower_best_floor": completed_floor},
                "$set": {"username": user.username, "first_name": user.first_name},
                "$setOnInsert": {"characters": [], "wins": 0, "total_guesses": 0, "favorites": []},
            },
            upsert=True,
        )
        note = f"✅ Correct! +{reward} 🟡 Neko Coins and +{xp_reward} XP."
    else:
        session["lives"] -= 1
        note = f"❌ Not quite. The answer was <b>{session['choices'][session['correct_index']]}</b>."

    if session["lives"] <= 0:
        _sessions.pop(owner_id, None)
        await query.answer("Tower run over!")
        await query.edit_message_text(
            "🏯 <b>TOWER RUN OVER</b>\n"
            f"Floors cleared: <b>{session['solved']}</b>\n"
            f"🟡 Neko Coins earned: <b>{session['earned']:,}</b>\n"
            f"🏆 Best floor reached: <b>{max(1, completed_floor)}</b>\n\n"
            "Your earned rewards are already in your balance. Try again after the cooldown!",
            parse_mode=ParseMode.HTML,
        )
        return

    session["question"], answer = _make_problem(session["floor"])
    session["choices"], session["correct_index"] = _make_choices(answer, session["floor"])
    await query.answer("Correct!" if correct else "Life used!")
    await query.edit_message_text(
        _floor_text(user.first_name, session, note),
        reply_markup=_keyboard(owner_id, session),
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("tower", tower, block=False))
application.add_handler(CommandHandler("tower_stop", tower_stop, block=False))
application.add_handler(CallbackQueryHandler(tower_callback, pattern=r"^tower:", block=False))
