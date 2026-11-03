import logging
import os
import random
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, BOT_USERNAME, GROUP_ID, PHOTO_URL, SUPPORT_CHAT, UPDATE_CHAT, OWNER_ID
from waifu import pm_users as _pm

MINIAPP_URL = os.environ.get("MINIAPP_URL", "https://adamwaifuminiapp.onrender.com")
LOGGER = logging.getLogger(__name__)


def _welcome(first_name: str, user_id: int) -> str:
    return (
        f"🌸 <b>𝗪𝗔𝗜𝗙𝗨𝗛𝗨𝗕 𝗩𝗘𝗥𝗦𝗘</b>  💮\n"
        f"<i>COLLECT ✦ TRADE ✦ DUEL ✦ DOMINATE</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Hey, <a href='tg://user?id={user_id}'><b>{escape(first_name)}</b></a>!\n"
        f"<i>Your anime collection starts with one brave guess.</i>\n\n"
        f"╭ 🎴 <b>THE DROP</b>\n"
        f"│ Characters appear randomly in group chats.\n"
        f"├ ⚡ <b>THE RACE</b>\n"
        f"│ Be quick — send /guess before someone else!\n"
        f"├ 💎 <b>THE COLLECTION</b>\n"
        f"│ Build your harem, discover rare finds & show them off.\n"
        f"╰ 🏆 <b>THE GLORY</b>\n"
        f"  Trade, duel and climb the leaderboard.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 <b>READY TO START?</b>\n"
        f"Add me to a group, then wait for the first character drop.\n"
        f"<i>One guess could make you a legend.</i>"
    )


HELP = (
    "📚 <b>𝗪𝗔𝗜𝗙𝗨𝗛𝗨𝗕 𝗚𝗨𝗜𝗗𝗘</b>\n"
    "<i>Your quick guide to the hunt.</i>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>🎴 CATCH & COLLECT</b>\n"
    "/guess — Claim the active character\n"
    "/harem — Browse your collection\n"
    "/check — View character details\n"
    "/profile — View your stats and level\n"
    "/rarity — View your rarity collection\n"
    "/hclaim — Claim a daily free character\n\n"
    "<b>💰 COINS & MARKET</b>\n"
    "/daily — Collect daily coins\n"
    "/balance — Check your balance\n"
    "/market — Browse character listings\n"
    "/sell — List a character for sale\n"
    "/buy — Purchase a listing\n"
    "/exchange — Exchange credits and shards\n\n"
    "<b>⚔️ PLAY WITH FRIENDS</b>\n"
    "/duel — Challenge a player (reply)\n"
    "/trade — Trade characters\n"
    "/gift — Send a character\n"
    "/marry — Propose marriage (reply)\n"
    "/spouse — View your spouse\n"
    "/divorce — End a marriage\n"
    "/couple — Reveal the couple of the day\n\n"
    "<b>🎮 MINI GAMES & RANKS</b>\n"
    "/quiz — Anime quiz (available group only)\n"
    "/nguess — Start name guessing\n"
    "/top — Top collectors\n"
    "/ctop — Group leaderboard\n\n"
    "<i>Tip: Add the bot to your group to start character drops!</i>"
)


def _kb(include_miniapp: bool = True, include_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if include_miniapp:
        rows.append([InlineKeyboardButton("✿ Open Mini App", web_app=WebAppInfo(url=MINIAPP_URL))])
    if include_admin:
        rows.append([InlineKeyboardButton("🔐 Owner Admin Panel", web_app=WebAppInfo(url=f"{MINIAPP_URL}/admin.html"))])
    rows.extend([
        [InlineKeyboardButton("➕ Add Me", url=f"https://t.me/{BOT_USERNAME}?startgroup=new")],
        [
            InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_CHAT}"),
            InlineKeyboardButton("📢 Updates", url=f"https://t.me/{UPDATE_CHAT}"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="help"),
            InlineKeyboardButton("👑 Owner", callback_data="owner"),
        ],
    ])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    existing = await _pm.find_one({"_id": u.id})
    if existing is None:
        await _pm.insert_one({"_id": u.id, "first_name": u.first_name, "username": u.username})
        try:
            await context.bot.send_message(
                GROUP_ID,
                f"🆕 New user: <a href='tg://user?id={u.id}'>{escape(u.first_name)}</a>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        patch = {}
        if existing.get("first_name") != u.first_name: patch["first_name"] = u.first_name
        if existing.get("username")   != u.username:   patch["username"]   = u.username
        if patch:
            await _pm.update_one({"_id": u.id}, {"$set": patch})

    photo   = random.choice(PHOTO_URL) if PHOTO_URL else None
    caption = _welcome(u.first_name, u.id) if update.effective_chat.type == "private" else "🎴 I'm alive! DM me for info."

    if photo:
        await context.bot.send_photo(
            update.effective_chat.id, photo=photo,
            caption=caption, reply_markup=_kb(include_miniapp=update.effective_chat.type == "private", include_admin=update.effective_chat.type == "private" and u.id == OWNER_ID), parse_mode=ParseMode.HTML,
        )
    else:
        await context.bot.send_message(
            update.effective_chat.id,
            text=caption, reply_markup=_kb(include_miniapp=update.effective_chat.type == "private", include_admin=update.effective_chat.type == "private" and u.id == OWNER_ID), parse_mode=ParseMode.HTML,
        )


async def _edit_or_send(q, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    """Edit the existing welcome message, or send a new one if Telegram rejects the edit."""
    try:
        if q.message and q.message.caption is not None:
            await q.edit_message_caption(
                caption=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
        else:
            await q.edit_message_text(
                text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
        return
    except Exception:
        LOGGER.exception("Could not edit welcome screen; sending a replacement message.")

    try:
        await q.message.reply_text(
            text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
        )
    except Exception:
        LOGGER.exception("Could not send replacement welcome/help message.")


async def button(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        LOGGER.debug("Callback query could not be acknowledged.", exc_info=True)

    user = q.from_user
    back_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Back to welcome", callback_data="back")]
    ])

    if q.data == "help":
        await _edit_or_send(q, HELP, back_kb)
    elif q.data == "owner":
        owner_text = (
            "👑 <b>BOT OWNER</b>\n\n"
            f"<a href='tg://user?id={OWNER_ID}'>Tap here to contact the owner</a>"
        )
        await _edit_or_send(q, owner_text, back_kb)
    elif q.data == "back":
        welcome_kb = _kb(
            include_miniapp=q.message.chat.type == "private",
            include_admin=user.id == OWNER_ID and q.message.chat.type == "private",
        )
        await _edit_or_send(q, _welcome(user.first_name, user.id), welcome_kb)


async def help_command(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(
        HELP,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Back to welcome", callback_data="back")]
        ]),
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("start", start, block=False))
application.add_handler(CommandHandler("help", help_command, block=False))
# Priority group ensures these buttons are handled before other callback handlers.
application.add_handler(
    CallbackQueryHandler(button, pattern=r"^(help|back|owner)$", block=False),
    group=-1,
)
            
