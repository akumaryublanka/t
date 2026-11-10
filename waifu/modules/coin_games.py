"""NekoVerse coin transfers and virtual-coin dice mini-games."""
import time
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection

_MIN_STAKE = 20
_MAX_STAKE = 5000
_COOLDOWN = 5
_LAST_PLAYED: dict[int, float] = {}


async def _ensure_user(user) -> None:
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


async def pay(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    sender = update.effective_user
    reply = message.reply_to_message
    if not reply or not reply.from_user:
        await message.reply_text("Reply to a player's message: <code>/pay amount</code>", parse_mode=ParseMode.HTML)
        return
    recipient = reply.from_user
    if recipient.is_bot or recipient.id == sender.id:
        await message.reply_text("❌ Choose another real player.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await message.reply_text("Usage: <code>/pay amount</code>", parse_mode=ParseMode.HTML)
        return
    amount = int(context.args[0])
    if amount < 1 or amount > 10000000:
        await message.reply_text("❌ Amount must be between 1 and 10,000,000 coins.")
        return

    await _ensure_user(sender)
    await _ensure_user(recipient)
    debit = await user_collection.update_one(
        {"id": sender.id, "coins": {"$gte": amount}},
        {"$inc": {"coins": -amount}},
    )
    if not debit.modified_count:
        player = await user_collection.find_one({"id": sender.id}) or {}
        await message.reply_text(f"❌ Not enough coins. Your balance is {int(player.get('coins', 0) or 0):,}.")
        return
    try:
        await user_collection.update_one({"id": recipient.id}, {"$inc": {"coins": amount}})
    except Exception:
        await user_collection.update_one({"id": sender.id}, {"$inc": {"coins": amount}})
        raise

    await message.reply_text(
        f"💸 <b>NEKO TRANSFER COMPLETE</b>\n\n"
        f"👤 From: <a href='tg://user?id={sender.id}'>{escape(sender.first_name)}</a>\n"
        f"👤 To: <a href='tg://user?id={recipient.id}'>{escape(recipient.first_name)}</a>\n"
        f"🟡 Amount: <b>{amount:,} Neko Coins</b>",
        parse_mode=ParseMode.HTML,
    )


async def _play_dice(update: Update, context: CallbackContext, *,
                     emoji: str, win_check, multiplier: float, game_name: str) -> None:
    message = update.effective_message
    user = update.effective_user
    if len(context.args) != 1 or not context.args[0].isdigit():
        await message.reply_text(
            f"Usage: <code>/{game_name.lower()} amount</code>\n"
            f"Stake: {_MIN_STAKE:,}–{_MAX_STAKE:,} Neko Coins.",
            parse_mode=ParseMode.HTML,
        )
        return
    stake = int(context.args[0])
    if not _MIN_STAKE <= stake <= _MAX_STAKE:
        await message.reply_text(f"❌ Stake must be {_MIN_STAKE:,}–{_MAX_STAKE:,} coins.")
        return
    now = time.time()
    wait = _COOLDOWN - (now - _LAST_PLAYED.get(user.id, 0))
    if wait > 0:
        await message.reply_text(f"⏳ Wait {wait:.1f}s before playing again.")
        return

    await _ensure_user(user)
    debit = await user_collection.update_one(
        {"id": user.id, "coins": {"$gte": stake}},
        {"$inc": {"coins": -stake}},
    )
    if not debit.modified_count:
        player = await user_collection.find_one({"id": user.id}) or {}
        await message.reply_text(f"❌ Not enough coins. Balance: {int(player.get('coins', 0) or 0):,}.")
        return

    _LAST_PLAYED[user.id] = now
    try:
        result = await context.bot.send_dice(chat_id=message.chat_id, emoji=emoji)
    except Exception:
        await user_collection.update_one({"id": user.id}, {"$inc": {"coins": stake}})
        _LAST_PLAYED.pop(user.id, None)
        await message.reply_text("❌ The dice game couldn't start; your stake was refunded.")
        return

    value = result.dice.value
    if win_check(value):
        payout = int(stake * multiplier)
        await user_collection.update_one({"id": user.id}, {"$inc": {"coins": payout, "wins": 1}})
        outcome = (
            f"🎉 <b>YOU WON!</b> Dice: <b>{value}</b>\n"
            f"🟡 Payout: <b>+{payout:,}</b> coins\n"
            f"✨ Net result: <b>+{payout-stake:,}</b> coins"
        )
    else:
        outcome = (
            f"💨 <b>Not this time!</b> Dice: <b>{value}</b>\n"
            f"🟡 Lost stake: <b>{stake:,}</b> coins. Better luck next round!"
        )
    player = await user_collection.find_one({"id": user.id}) or {}
    await message.reply_text(
        f"🎮 <b>NEKOVERSE {escape(game_name.upper())}</b>\n\n"
        f"{outcome}\n"
        f"🏦 Balance: <b>{int(player.get('coins', 0) or 0):,}</b>",
        parse_mode=ParseMode.HTML,
    )


async def bet(update: Update, context: CallbackContext) -> None:
    await _play_dice(update, context, emoji="🎲", win_check=lambda value: value >= 5, multiplier=2.7, game_name="Bet")


async def dart(update: Update, context: CallbackContext) -> None:
    await _play_dice(update, context, emoji="🎯", win_check=lambda value: value == 6, multiplier=5.0, game_name="Dart")


async def bowl(update: Update, context: CallbackContext) -> None:
    await _play_dice(update, context, emoji="🎳", win_check=lambda value: value == 6, multiplier=5.0, game_name="Bowl")


async def basket(update: Update, context: CallbackContext) -> None:
    await _play_dice(update, context, emoji="🏀", win_check=lambda value: value >= 4, multiplier=2.2, game_name="Basket")


application.add_handler(CommandHandler("pay", pay, block=False))
application.add_handler(CommandHandler("bet", bet, block=False))
application.add_handler(CommandHandler("dart", dart, block=False))
application.add_handler(CommandHandler("bowl", bowl, block=False))
application.add_handler(CommandHandler("basket", basket, block=False))
