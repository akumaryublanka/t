import os
import random
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, BOT_USERNAME, GROUP_ID, PHOTO_URL, SUPPORT_CHAT, UPDATE_CHAT, OWNER_ID
from waifu import pm_users as _pm

MINIAPP_URL = os.environ.get("MINIAPP_URL", "https://adamwaifuminiapp.onrender.com")


def _welcome(first_name: str, user_id: int) -> str:
    return (
        f"🌸 <b>𝗪𝗮𝗶𝗳𝘂𝗛𝘂𝗯 𝗩𝗲𝗿𝘀𝗲 💮</b>\n\n"
        f"✦ <i>Where legends are collected & waifus are claimed...</i> ✦\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👋 <b>𝗪𝗲𝗹𝗰𝗼𝗺𝗲, <a href='tg://user?id={user_id}'>{escape(first_name)}</a>!</b>\n\n"
        f"⚡ <b>𝗛𝗼𝘄 𝘁𝗼 𝗣𝗹𝗮𝘆:</b>\n"
        f"┣ 🎴 <b>𝗖𝗵𝗮𝗿𝗮𝗰𝘁𝗲𝗿𝘀</b> drop in groups randomly\n"
        f"┣ 💬 <b>𝗧𝘆𝗽𝗲</b> /guess to claim them\n"
        f"┣ 👑 <b>𝗕𝘂𝗶𝗹𝗱</b> your ultimate harem\n"
        f"┗ 🏆 <b>𝗙𝗹𝗲𝘅</b> your rarest waifus!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <b>𝗔𝗱𝗱 𝗺𝗲 𝘁𝗼 𝘆𝗼𝘂𝗿 𝗴𝗿𝗼𝘂𝗽 𝗮𝗻𝗱 𝗹𝗲𝘁 𝘁𝗵𝗲 𝗵𝘂𝗻𝘁 𝗯𝗲𝗴𝗶𝗻!</b>"
    )


HELP = (
    "📖 <b>Commands</b>\n\n"
    "<b>🎮 Game</b>\n"
    "/guess — Claim the active character\n"
    "/harem — Your collection (paginated)\n"
    "/fav [id] — Set favourite character\n"
    "/check [id] — Check character details\n"
    "/hclaim — Daily free character claim\n"
    "/profile — Your stats & level\n\n"
    "<b>💰 Economy</b>\n"
    "/daily — Claim daily coins\n"
    "/weekly — Claim weekly bonus coins\n"
    "/balance — Check your coins\n"
    "/market — Browse listings\n"
    "/sell [id] [price] — List a character\n"
    "/buy [listing_id] — Buy from market\n\n"
    "<b>🎮 Games</b>\n"
    "/quiz — Anime character quiz\n"
    "/nguess — Name guessing game\n"
    "/ship @user — Love compatibility 💕\n\n"
    "<b>⚔️ Social</b>\n"
    "/marry — Propose to someone 💍\n"
    "/divorce — End marriage\n"
    "/couple — Couple of the day\n"
    "/trade [char_id] — Trade (reply to user)\n"
    "/gift [char_id] — Gift a character\n"
    "/duel — Challenge to duel\n\n"
    "<b>📊 Leaderboards</b>\n"
    "/top — Top collectors\n"
    "/ctop — This group's top\n"
    "/TopGroups — Most active groups\n\n"
    "<b>⚙️ Settings</b>\n"
    "/changetime [n] — Drop every n messages (admin)\n"
    "/resettime — Reset to default (admin)\n"
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


async def button(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    u       = q.from_user
    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])

    try:
        if q.data == "help":
            await q.edit_message_caption(caption=HELP, reply_markup=back_kb, parse_mode=ParseMode.HTML)
        elif q.data == "owner":
            owner_text = f"👑 <b>Bot Owner</b>\n\n<a href='tg://user?id={OWNER_ID}'>Contact the owner on Telegram</a>"
            await q.edit_message_caption(caption=owner_text, reply_markup=back_kb, parse_mode=ParseMode.HTML)
        elif q.data == "back":
            await q.edit_message_caption(
                caption=_welcome(u.first_name, u.id),
                reply_markup=_kb(include_admin=u.id == OWNER_ID and q.message.chat.type == "private"),
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        try:
            if q.data == "help":
                await q.edit_message_text(HELP, reply_markup=back_kb, parse_mode=ParseMode.HTML)
            elif q.data == "owner":
                owner_text = f"👑 <b>Bot Owner</b>\n\n<a href='tg://user?id={OWNER_ID}'>Contact the owner on Telegram</a>"
                await q.edit_message_text(owner_text, reply_markup=back_kb, parse_mode=ParseMode.HTML)
            elif q.data == "back":
                await q.edit_message_text(
                    _welcome(u.first_name, u.id),
                    reply_markup=_kb(include_admin=u.id == OWNER_ID and q.message.chat.type == "private"),
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            pass


application.add_handler(CommandHandler("start", start, block=False))
application.add_handler(CallbackQueryHandler(button, pattern=r"^(help|back|owner)$", block=False))
            
