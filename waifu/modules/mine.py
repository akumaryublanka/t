"""
modules/mines.py — Minesweeper Game
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• /mines        → Start game (500 Grey Credits entry)
• 4x4 grid, 3 mines hidden
• 2 mine hits = Game Over
• Reveal safe tiles to earn more Grey Credits
• /cashout → Cash out your earned Grey Credits at any time

Rewards:
  1-4  safe = 1.2x
  5-8  safe = 1.5x
  9-11 safe = 2x
  12-13 safe = 3x
"""

import random
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, db
from waifu.modules.auto_delete import schedule_delete, MINES_DELAY

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ENTRY_FEE   = 500
GRID_SIZE   = 4
TOTAL_CELLS = GRID_SIZE * GRID_SIZE  # 16
MINE_COUNT  = 3
MAX_HITS    = 2  # 2 mine hits = game over

# Multiplier values are snapshotted into each game so an admin change
# never alters a round midway through play.
_DEFAULT_MULTIPLIERS = {"1_4": 120, "5_8": 150, "9_11": 200, "12_13": 300}


def _multiplier(safe: int, multipliers: dict | None = None) -> float:
    values = multipliers or _DEFAULT_MULTIPLIERS
    if safe <= 4:
        rate = values.get("1_4", 120)
    elif safe <= 8:
        rate = values.get("5_8", 150)
    elif safe <= 11:
        rate = values.get("9_11", 200)
    else:
        rate = values.get("12_13", 300)
    return max(0, int(rate)) / 100


def _reward(safe: int, entry_fee: int = ENTRY_FEE, multipliers: dict | None = None) -> int:
    return int(max(0, entry_fee) * _multiplier(safe, multipliers))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GAME STATE (in-memory per user)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# { user_id: { "mines": set, "revealed": set, "hits": int, "safe": int } }
_games: dict[int, dict] = {}


def _new_game(entry_fee: int = ENTRY_FEE, multipliers: dict | None = None) -> dict:
    mines = set(random.sample(range(TOTAL_CELLS), MINE_COUNT))
    return {
        "mines":    mines,
        "revealed": set(),
        "hits":     0,
        "safe":     0,
        "over":     False,
        "won":      False,
        "entry_fee": max(0, int(entry_fee)),
        "multipliers": dict(multipliers or _DEFAULT_MULTIPLIERS),
    }


def _build_keyboard(game: dict, reveal_all: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for row in range(GRID_SIZE):
        line = []
        for col in range(GRID_SIZE):
            idx = row * GRID_SIZE + col
            if idx in game["revealed"] or reveal_all:
                if idx in game["mines"]:
                    label = "💣"
                else:
                    label = "🔷"
            else:
                label = "⬛"
            line.append(InlineKeyboardButton(
                label,
                callback_data=f"mines:{idx}"
            ))
        rows.append(line)

    # Bottom buttons
    if not game["over"]:
        rows.append([
            InlineKeyboardButton("💰 Cash Out", callback_data="mines:cashout"),
            InlineKeyboardButton("❌ Quit",     callback_data="mines:quit"),
        ])
    return InlineKeyboardMarkup(rows)


def _game_text(user_name: str, game: dict) -> str:
    hits      = game["hits"]
    safe      = game["safe"]
    entry_fee = max(0, int(game.get("entry_fee", ENTRY_FEE)))
    multipliers = game.get("multipliers") or _DEFAULT_MULTIPLIERS
    reward    = _reward(safe, entry_fee, multipliers)
    mult      = _multiplier(safe, multipliers)
    remaining = MAX_HITS - hits

    if game["over"] and not game["won"]:
        return (
            f"💣 <b>BOOM! Game Over!</b>\n\n"
            f"👤 {escape(user_name)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"₲ <b>{entry_fee} Grey Credits Lost</b>\n"
            f"✅ Safe tiles: <b>{safe}</b>\n"
            f"💣 Mine hits: <b>{hits}</b>\n\n"
            f"Better luck next time! 😢"
        )
    if game["won"]:
        return (
            f"🎉 <b>Amazing! You cleared the board!</b>\n\n"
            f"👤 {escape(user_name)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ Safe tiles: <b>{safe}</b>\n"
            f"🎁 Reward: <b>₲ {reward} Grey Credits</b> ({mult}x)\n\n"
            f"🏆 Legend! You avoided all the mines!"
        )

    return (
        f"💣 <b>Minesweeper!</b>\n\n"
        f"👤 {escape(user_name)}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 {MINE_COUNT} mines  |  Entry Fee: ₲ {entry_fee} Grey Credits\n"
        f"💣 {MAX_HITS} mine hits = Game Over!\n\n"
        f"<b>💎 Current Reward:</b>\n"
        f"• {safe} safe = <b>₲ {reward} Grey Credits</b> ({mult}x)\n\n"
        f"❤️ Lives: {'❤️' * remaining}{'🖤' * hits}\n"
        f"✅ Safe so far: <b>{safe}</b>"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def mines_command(update: Update, context: CallbackContext) -> None:
    user    = update.effective_user
    message = update.effective_message

    settings_doc = await db["miniapp_settings"].find_one({"_id": "game"})
    features_doc = await db["miniapp_settings"].find_one({"_id": "features"})
    game_settings = (settings_doc or {}).get("data") or {}
    feature_settings = (features_doc or {}).get("data") or {}
    if game_settings.get("mines_enabled", True) is False or feature_settings.get("mines", True) is False:
        await message.reply_text("⏸️ Mines is temporarily disabled by the bot owner.")
        return
    try:
        entry_fee = max(0, min(100000000, int(game_settings.get("bot_mines_entry_fee", ENTRY_FEE))))
        multipliers = {
            "1_4": max(0, min(1000, int(game_settings.get("bot_mines_multiplier_1_4_percent", 120)))),
            "5_8": max(0, min(1000, int(game_settings.get("bot_mines_multiplier_5_8_percent", 150)))),
            "9_11": max(0, min(1000, int(game_settings.get("bot_mines_multiplier_9_11_percent", 200)))),
            "12_13": max(0, min(1000, int(game_settings.get("bot_mines_multiplier_12_13_percent", 300)))),
        }
    except (TypeError, ValueError):
        entry_fee, multipliers = ENTRY_FEE, dict(_DEFAULT_MULTIPLIERS)

    # Already in game?
    if user.id in _games and not _games[user.id]["over"]:
        await message.reply_text(
            "⚠️ Your game is already running!\n"
            "Finish it first or use <code>/cashout</code>.",
            parse_mode=ParseMode.HTML
        )
        return

    # Check coins
    u_doc = await user_collection.find_one({"id": user.id})
    if not u_doc:
        await message.reply_text("❌ Start the bot first with /start!")
        return

    coins = u_doc.get("coins", 0)
    if coins < entry_fee:
        await message.reply_text(
            f"❌ You need <b>₲ {entry_fee} Grey Credits</b> to play.\n"
            f"Current balance: <b>₲ {coins} Grey Credits</b>",
            parse_mode=ParseMode.HTML
        )
        return

    # Deduct entry fee atomically to avoid concurrent balance changes.
    debit = await user_collection.update_one(
        {"id": user.id, "coins": {"$gte": entry_fee}},
        {"$inc": {"coins": -entry_fee}}
    )
    if not debit.modified_count:
        await message.reply_text("❌ Your balance changed; please try starting Mines again.")
        return

    # Start game with a snapshot of the current owner settings.
    game = _new_game(entry_fee, multipliers)
    _games[user.id] = game

    kb   = _build_keyboard(game)
    text = _game_text(user.first_name, game)

    sent = await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    schedule_delete(
        context.bot,
        update.effective_chat.id,
        sent.message_id,
        MINES_DELAY,
        on_complete=lambda: _games.pop(user.id, None) if _games.get(user.id) is game else None,
    )


async def cashout_command(update: Update, context: CallbackContext) -> None:
    user    = update.effective_user
    message = update.effective_message

    game = _games.get(user.id)
    if not game or game["over"]:
        await message.reply_text("❌ No active game! Start one with /mines.")
        return

    if game["safe"] == 0:
        await message.reply_text("⚠️ Reveal at least one tile before cashing out!")
        return

    reward = _reward(game["safe"], game.get("entry_fee", ENTRY_FEE), game.get("multipliers"))
    game["over"] = True
    game["won"]  = True

    await user_collection.update_one(
        {"id": user.id},
        {"$inc": {"coins": reward}}
    )

    kb = _build_keyboard(game, reveal_all=True)
    sent = await message.reply_text(
        f"💰 <b>Cashed Out!</b>\n\n"
        f"✅ Safe tiles: <b>{game['safe']}</b>\n"
        f"🎁 Reward: <b>₲ {reward} Grey Credits</b> ({_multiplier(game['safe'], game.get('multipliers'))}x)\n\n"
        f"Smart move! 😎",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )
    schedule_delete(context.bot, update.effective_chat.id, sent.message_id, MINES_DELAY)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CALLBACK — tile click
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def mines_callback(update: Update, context: CallbackContext) -> None:
    q    = update.callback_query
    user = q.from_user
    await q.answer()

    data = q.data  # "mines:0" .. "mines:15" | "mines:cashout" | "mines:quit"
    action = data.split(":")[1]

    game = _games.get(user.id)
    if not game:
        await q.answer("❌ Game not found! Start one with /mines.", show_alert=True)
        return

    if game["over"]:
        await q.answer("This game has already ended!", show_alert=True)
        return

    # ── Cash out ──────────────────────────────────────────────────────
    if action == "cashout":
        if game["safe"] == 0:
            await q.answer("⚠️ Reveal at least one tile first!", show_alert=True)
            return
        reward       = _reward(game["safe"], game.get("entry_fee", ENTRY_FEE), game.get("multipliers"))
        game["over"] = True
        game["won"]  = True
        await user_collection.update_one(
            {"id": user.id}, {"$inc": {"coins": reward}}
        )
        kb   = _build_keyboard(game, reveal_all=True)
        text = (
            f"💰 <b>Cashed Out!</b>\n\n"
            f"✅ Safe tiles: <b>{game['safe']}</b>\n"
            f"🎁 Reward: <b>₲ {reward} Grey Credits</b> ({_multiplier(game['safe'], game.get('multipliers'))}x)\n\n"
            f"Smart move! 😎"
        )
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    # ── Quit ──────────────────────────────────────────────────────────
    if action == "quit":
        game["over"] = True
        kb   = _build_keyboard(game, reveal_all=True)
        try:
            await q.edit_message_text(
                f"🏳️ You quit the game.\n₲ {game.get('entry_fee', ENTRY_FEE)} Grey Credits lost...",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
        except Exception:
            pass
        return

    # ── Tile click ────────────────────────────────────────────────────
    try:
        idx = int(action)
    except ValueError:
        return

    if idx in game["revealed"]:
        await q.answer("This tile has already been revealed!", show_alert=True)
        return

    game["revealed"].add(idx)

    if idx in game["mines"]:
        # Mine hit!
        game["hits"] += 1
        await q.answer("💣 BOOM! Mine mili!", show_alert=True)

        if game["hits"] >= MAX_HITS:
            # Game over
            game["over"] = True
            kb   = _build_keyboard(game, reveal_all=True)
            text = _game_text(user.first_name, game)
            try:
                await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception:
                pass
            return
    else:
        game["safe"] += 1
        await q.answer(f"✅ Safe! +1 tile")

        # All safe tiles revealed?
        total_safe = TOTAL_CELLS - MINE_COUNT
        if game["safe"] >= total_safe:
            game["over"] = True
            game["won"]  = True
            reward = _reward(game["safe"], game.get("entry_fee", ENTRY_FEE), game.get("multipliers"))
            await user_collection.update_one(
                {"id": user.id}, {"$inc": {"coins": reward}}
            )
            kb   = _build_keyboard(game, reveal_all=True)
            text = _game_text(user.first_name, game)
            try:
                await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception:
                pass
            return

    # Update board
    kb   = _build_keyboard(game)
    text = _game_text(user.first_name, game)
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  REGISTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

application.add_handler(CommandHandler("mines", mines_command, block=False))
application.add_handler(CommandHandler("cashout", cashout_command, block=False))
application.add_handler(CallbackQueryHandler(mines_callback, pattern=r"^mines:", block=False))
    
