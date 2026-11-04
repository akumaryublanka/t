"""
modules/economy.py — Daily coins, balance, and marketplace.
"""
import math
import time
from bson import ObjectId
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, market_collection, db
from waifu.config import Config

_DAILY_COOLDOWN = 86_400   # 24 hours in seconds
_PAGE = 8

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(secs: int) -> str:
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


async def _ensure_user(user_id: int, u) -> dict:
    doc = await user_collection.find_one({"id": user_id})
    if not doc:
        settings_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
        settings = (settings_doc or {}).get("data") or {}
        try:
            starting_coins = max(0, min(100000000, int(settings.get("starting_coins", 0))))
        except (TypeError, ValueError):
            starting_coins = 0
        doc = {
            "id": user_id, "username": u.username,
            "first_name": u.first_name, "characters": [],
            "coins": starting_coins, "xp": 0, "wins": 0,
            "total_guesses": 0, "favorites": [],
        }
        await user_collection.insert_one(doc)
    return doc


async def _market_enabled() -> bool:
    game_doc = await db["miniapp_settings"].find_one({"_id": "game"})
    feature_doc = await db["miniapp_settings"].find_one({"_id": "features"})
    game = (game_doc or {}).get("data") or {}
    features = (feature_doc or {}).get("data") or {}
    return game.get("market_enabled", True) is not False and features.get("market", True) is not False


# ── /balance ──────────────────────────────────────────────────────────────────

async def balance(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    await update.message.reply_text(
        f"💰 <b>{escape(u.first_name)}'s Balance</b>\n\n"
        f"🟡 Neko Coins: <b>{doc.get('coins', 0):,}</b>\n"
        f"💎 Neko Gems: <b>{doc.get('grey_shards', 0):,}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /daily ────────────────────────────────────────────────────────────────────

async def daily(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    now = time.time()
    last = doc.get("last_daily", 0)
    settings_doc = await db["miniapp_settings"].find_one({"_id": "game"})
    settings = (settings_doc or {}).get("data") or {}
    try:
        cooldown = max(60, min(604800, int(settings.get("daily_cooldown_seconds", _DAILY_COOLDOWN))))
    except (TypeError, ValueError):
        cooldown = _DAILY_COOLDOWN

    if now - last < cooldown:
        remaining = int(cooldown - (now - last))
        await update.message.reply_text(
            f"⏳ Daily already claimed!\nCome back in <b>{_fmt_time(remaining)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        reward = max(0, min(100000000, int(settings.get("daily_reward", Config.DAILY_NEKO_COINS))))
    except (TypeError, ValueError):
        reward = Config.DAILY_NEKO_COINS
    await user_collection.update_one(
        {"id": u.id},
        {"$inc": {"coins": reward}, "$set": {"last_daily": now}},
    )
    await update.message.reply_text(
        f"🎁 <b>Daily reward!</b>\n\n"
        f"You received <b>{reward:,} 🟡 Neko Coins</b>\n"
        f"Current Neko Coins: <b>{doc.get('coins', 0) + reward:,}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /sell ─────────────────────────────────────────────────────────────────────

async def sell(update: Update, context: CallbackContext) -> None:
    if not await _market_enabled():
            await update.message.reply_text("🛒 The marketplace is temporarily disabled by the bot owner.")
            return

    u = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: <code>/sell [char_id] [price]</code>", parse_mode=ParseMode.HTML)
        return

    char_id, price_str = context.args
    if not price_str.isdigit() or int(price_str) <= 0:
        await update.message.reply_text("❌ Price must be a positive number.")
        return
    price = int(price_str)

    doc = await user_collection.find_one({"id": u.id})
    if not doc:
        await update.message.reply_text("❌ You have no characters.")
        return

    char = next((c for c in doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text("❌ That character isn't in your collection.")
        return

    # Remove from user's harem (escrow while listed)
    await user_collection.update_one(
        {"id": u.id},
        {"$pull": {"characters": {"id": char_id}}},
    )
    listing = {
        "seller_id":   u.id,
        "seller_name": u.first_name,
        "char_id":     char_id,
        "char":        char,
        "price":       price,
        "listed_at":   time.time(),
    }
    result = await market_collection.insert_one(listing)

    await update.message.reply_text(
        f"🏪 <b>{escape(char['name'])}</b> listed for <b>{price:,} 🟡 Neko Coins</b>!\n"
        f"Listing ID: <code>{result.inserted_id}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /market ───────────────────────────────────────────────────────────────────

async def market(update: Update, context: CallbackContext, page: int = 0) -> None:
    if not await _market_enabled():
            await update.effective_message.reply_text("🛒 The marketplace is temporarily disabled by the bot owner.")
            return

    args  = context.args if hasattr(context, "args") and context.args else []
    if args and args[0].isdigit():
        page = int(args[0]) - 1

    total   = await market_collection.count_documents({})
    if total == 0:
        await update.message.reply_text("🏪 The market is empty right now.")
        return

    total_pages = max(1, math.ceil(total / _PAGE))
    page = max(0, min(page, total_pages - 1))

    listings = await market_collection.find({}).sort("price", 1).skip(page * _PAGE).limit(_PAGE).to_list(_PAGE)

    lines = [f"🏪 <b>Market</b>  (page {page+1}/{total_pages})\n"]
    for lst in listings:
        char  = lst["char"]
        lines.append(
            f"{char.get('rarity','🎴')}  <b>{escape(char['name'])}</b>  "
            f"— <b>{lst['price']:,} 🟡</b>\n"
            f"   Seller: {escape(lst['seller_name'])}  |  "
            f"<code>/buy {lst['_id']}</code>"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"market:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"market:{page+1}"))

    kb = InlineKeyboardMarkup([nav] if nav else [])
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)


async def market_page_cb(update: Update, context: CallbackContext) -> None:
    if not await _market_enabled():
            await update.callback_query.answer("Marketplace is temporarily disabled.", show_alert=True)
            return

    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[1])

    total       = await market_collection.count_documents({})
    total_pages = max(1, math.ceil(total / _PAGE))
    page        = max(0, min(page, total_pages - 1))
    listings    = await market_collection.find({}).sort("price", 1).skip(page * _PAGE).limit(_PAGE).to_list(_PAGE)

    lines = [f"🏪 <b>Market</b>  (page {page+1}/{total_pages})\n"]
    for lst in listings:
        char = lst["char"]
        lines.append(
            f"{char.get('rarity','🎴')}  <b>{escape(char['name'])}</b>  "
            f"— <b>{lst['price']:,} 🟡</b>\n"
            f"   Seller: {escape(lst['seller_name'])}  |  "
            f"<code>/buy {lst['_id']}</code>"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"market:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"market:{page+1}"))
    kb = InlineKeyboardMarkup([nav])
    try:
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass


# ── /buy ──────────────────────────────────────────────────────────────────────

async def buy(update: Update, context: CallbackContext) -> None:
    if not await _market_enabled():
            await update.message.reply_text("🛒 The marketplace is temporarily disabled by the bot owner.")
            return

    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/buy [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found or already sold.")
        return
    if listing["seller_id"] == u.id:
        await update.message.reply_text("❌ You can't buy your own listing.")
        return

    buyer = await user_collection.find_one({"id": u.id})
    if not buyer or buyer.get("coins", 0) < listing["price"]:
        await update.message.reply_text(
            f"❌ Not enough 🟡 Neko Coins. Need <b>{listing['price']:,}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    economy_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
    economy_settings = (economy_doc or {}).get("data") or {}
    try:
        fee_percent = max(0, min(100, int(economy_settings.get("market_fee_percent", 0))))
    except (TypeError, ValueError):
        fee_percent = 0
    fee = min(int(listing["price"]), (int(listing["price"]) * fee_percent) // 100)
    seller_payout = int(listing["price"]) - fee

    # Atomic exchange from the buyer's balance; the listing is removed only
    # after the balance update succeeds.
    debit = await user_collection.update_one(
        {"id": u.id, "coins": {"$gte": int(listing["price"])}},
        {"$inc": {"coins": -int(listing["price"])}, "$push": {"characters": listing["char"]}},
    )
    if not debit.modified_count:
        await update.message.reply_text("❌ Your balance changed; please try buying again.")
        return
    if seller_payout:
        await user_collection.update_one({"id": listing["seller_id"]}, {"$inc": {"coins": seller_payout}})
    await market_collection.delete_one({"_id": oid})

    char = listing["char"]
    await update.message.reply_text(
        f"✅ You bought <b>{escape(char['name'])}</b> for <b>{listing['price']:,} 🟡 Neko Coins</b>!\\n"
        f"Seller received <b>{seller_payout:,}</b> Neko Coins (market fee: {fee:,}).",
        parse_mode=ParseMode.HTML,
    )


# ── /delist ───────────────────────────────────────────────────────────────────

async def delist(update: Update, context: CallbackContext) -> None:
    if not await _market_enabled():
            await update.message.reply_text("🛒 The marketplace is temporarily disabled by the bot owner.")
            return

    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/delist [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found.")
        return
    if listing["seller_id"] != u.id:
        await update.message.reply_text("❌ That's not your listing.")
        return

    await market_collection.delete_one({"_id": oid})
    await user_collection.update_one(
        {"id": u.id}, {"$push": {"characters": listing["char"]}})
    await update.message.reply_text("✅ Listing removed. Character returned to your harem.")


# ── /exchange ────────────────────────────────────────────────────────────────
# Usage:
#   /exchange coins 1000  -> 1 Neko Gem
#   /exgem 1              -> 900 Neko Coins
#   /exchange 1000          -> shorthand for coins -> gems

async def exchange(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    doc = await _ensure_user(u.id, u)
    args = list(context.args or [])

    if len(args) == 1 and args[0].isdigit():
        direction, amount_text = "coins", args[0]
    elif len(args) == 2:
        direction, amount_text = args[0].lower(), args[1]
    else:
        await update.message.reply_text(
            "💱 <b>NekoVerse Exchange</b>\n\n"
            f"🟡 {Config.NEKO_COINS_PER_GEM:,} Neko Coins → 1 💎 Neko Gem\n"
            f"💎 1 Neko Gem → {Config.NEKO_COINS_PER_REVERSE_GEM:,} 🟡 Neko Coins\n\n"
            "Use: <code>/exchange coins 1000</code>\n"
            "Or: <code>/exgem 1</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not amount_text.isdigit() or int(amount_text) <= 0:
        await update.message.reply_text("❌ Amount must be a positive whole number.")
        return
    amount = int(amount_text)
    economy_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
    economy_settings = (economy_doc or {}).get("data") or {}
    try:
        max_exchange = max(1, min(1000000000, int(economy_settings.get("max_transfer_coins", 100000))))
    except (TypeError, ValueError):
        max_exchange = 100000

    if direction in ("credit", "credits", "coin", "coins"):
        if amount > max_exchange:
            await update.message.reply_text(
                f"❌ The configured exchange limit is {max_exchange:,} Neko Coins per exchange."
            )
            return
        if amount < Config.NEKO_COINS_PER_GEM or amount % Config.NEKO_COINS_PER_GEM != 0:
            await update.message.reply_text(
                f"❌ Neko Coins must be exchanged in multiples of {Config.NEKO_COINS_PER_GEM:,}.\n"
                "Example: <code>/exchange coins 1000</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        shards = amount // Config.NEKO_COINS_PER_GEM
        result = await user_collection.update_one(
            {"id": u.id, "coins": {"$gte": amount}},
            {"$inc": {"coins": -amount, "grey_shards": shards}},
        )
        if not result.modified_count:
            current = await user_collection.find_one({"id": u.id}) or doc
            await update.message.reply_text(
                f"❌ Not enough 🟡 Neko Coins. You have <b>{current.get('coins', 0):,}</b>.",
                parse_mode=ParseMode.HTML,
            )
            return
        current = await user_collection.find_one({"id": u.id})
        await update.message.reply_text(
            f"<b>𝐍𝐞𝐤𝐨𝐕𝐞𝐫𝐬𝐞:</b>\n\n"
            f"You exchanged <b>{amount:,} 🟡 Neko Coins</b> and received "
            f"<b>{shards:,} 💎 Neko Gem{'s' if shards != 1 else ''}!</b>\n\n"
            f"<b>New Balance:</b>\n"
            f"🟡 Neko Coins: <b>{current.get('coins', 0):,}</b>\n"
            f"💎 Neko Gems: <b>{current.get('grey_shards', 0):,}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if direction in ("shard", "shards", "gem", "gems"):
        credits = amount * Config.NEKO_COINS_PER_REVERSE_GEM
        if credits > max_exchange:
            await update.message.reply_text(
                f"❌ The configured exchange limit is {max_exchange:,} Neko Coins per exchange."
            )
            return
        result = await user_collection.update_one(
            {"id": u.id, "grey_shards": {"$gte": amount}},
            {"$inc": {"grey_shards": -amount, "coins": credits}},
        )
        if not result.modified_count:
            current = await user_collection.find_one({"id": u.id}) or doc
            await update.message.reply_text(
                f"❌ Not enough 💎 Neko Gems. You have <b>{current.get('grey_shards', 0):,}</b>.",
                parse_mode=ParseMode.HTML,
            )
            return
        current = await user_collection.find_one({"id": u.id})
        await update.message.reply_text(
            "<b>𝐍𝐄𝐊𝐎𝐕𝐄𝐑𝐒𝐄</b>\n\n"
            f"You exchanged <b>{amount:,} 💎 Neko Gem{'s' if amount != 1 else ''}</b> and received "
            f"<b>{credits:,} 🟡 Neko Coins!</b>\n\n"
            f"<b>New Balance:</b>\n"
            f"🟡 Neko Coins: <b>{current.get('coins', 0):,}</b>\n"
            f"💎 Neko Gems: <b>{current.get('grey_shards', 0):,}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        "❌ Choose <code>coins</code> or <code>gems</code>.\n"
        "Example: <code>/exchange coins 1000</code> or <code>/exgem 1</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /exgem ─────────────────────────────────────────────────────────────────
# Convert Neko Gems back into Neko Coins using the configured reverse exchange rate.

async def exgem(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    message = update.effective_message
    if not u or not message:
        return

    args = list(context.args or [])
    if len(args) != 1 or not args[0].isdigit() or int(args[0]) <= 0:
        await message.reply_text(
            "💎 <b>Neko Gem Exchange</b>\n\n"
            f"1 💎 Neko Gem → {Config.NEKO_COINS_PER_REVERSE_GEM:,} 🟡 Neko Coins\n\n"
            "Usage: <code>/exgem 1</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    amount = int(args[0])
    doc = await _ensure_user(u.id, u)

    economy_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
    economy_settings = (economy_doc or {}).get("data") or {}
    try:
        max_exchange = max(1, min(1000000000, int(economy_settings.get("max_transfer_coins", 100000))))
    except (TypeError, ValueError):
        max_exchange = 100000

    credits = amount * Config.NEKO_COINS_PER_REVERSE_GEM
    if credits > max_exchange:
        await message.reply_text(
            f"❌ The configured exchange limit is {max_exchange:,} Neko Coins per exchange."
        )
        return

    result = await user_collection.update_one(
        {"id": u.id, "grey_shards": {"$gte": amount}},
        {"$inc": {"grey_shards": -amount, "coins": credits}},
    )
    if not result.modified_count:
        current = await user_collection.find_one({"id": u.id}) or doc
        await message.reply_text(
            f"❌ Not enough 💎 Neko Gems. You have "
            f"<b>{current.get('grey_shards', 0):,}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    current = await user_collection.find_one({"id": u.id}) or doc
    await message.reply_text(
        "<b>💱 NekoVerse Exchange Complete</b>\n\n"
        f"You exchanged <b>{amount:,} 💎 Neko Gem{'s' if amount != 1 else ''}</b> "
        f"and received <b>{credits:,} 🟡 Neko Coins</b>.\n\n"
        "<b>New Balance:</b>\n"
        f"🟡 Neko Coins: <b>{current.get('coins', 0):,}</b>\n"
        f"💎 Neko Gems: <b>{current.get('grey_shards', 0):,}</b>",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("balance", balance, block=False))
application.add_handler(CommandHandler("daily",   daily,   block=False))
application.add_handler(CommandHandler("exchange", exchange, block=False))
application.add_handler(CommandHandler("exgem", exgem, block=False))
application.add_handler(CommandHandler("exshard", exgem, block=False))  # legacy alias
application.add_handler(CommandHandler("sell",    sell,    block=False))
application.add_handler(CommandHandler("market",  market,  block=False))
application.add_handler(CommandHandler("buy",     buy,     block=False))
application.add_handler(CommandHandler("delist",  delist,  block=False))
application.add_handler(CallbackQueryHandler(market_page_cb, pattern=r"^market:\d+$", block=False))
