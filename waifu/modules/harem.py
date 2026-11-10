"""
modules/harem.py — Paginated collection with inline action buttons per character.
"""
import logging
import math
from html import escape
from itertools import groupby

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, waifu_collection
from waifu.config import Config

logger = logging.getLogger(__name__)

_PAGE = 15
_IMAGE_PROXY = "https://t-5vdc.onrender.com/api/miniapp/character-image/{}?wm=1"
# Always use the same rarity names/icons as /upload and /uploadchar.
# This prevents the Harem display from falling behind when rarities change.
_MEDALS = {
    rarity: rarity.split(" ", 1)[0]
    for rarity in Config.RARITY_MAP.values()
}
def _rarity_icon(rarity: str) -> str:
    raw = str(rarity or "").strip()
    if raw.isdigit():
        canonical = Config.RARITY_MAP.get(int(raw), raw)
    else:
        canonical = Config.RARITY_LEGACY_MAP.get(raw, raw)
    return _MEDALS.get(canonical, "🎴")


async def _anime_totals(animes: list[str]) -> dict[str, int]:
    pipeline = [
        {"$match": {"anime": {"$in": animes}}},
        {"$group": {"_id": "$anime", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in waifu_collection.aggregate(pipeline)}


def _harem_photo_url(char: dict | None) -> str | None:
    if not char:
        return None
    image = str(char.get("img_url", "") or "").strip()
    char_id = str(char.get("id", "") or "").strip()
    # Prefer Telegram-backed proxy for known IDs; legacy hosted URLs may expire.
    if char_id:
        return _IMAGE_PROXY.format(char_id)
    return image if image.startswith(("https://", "http://")) else None


async def _build_page(user_id: int, page: int) -> tuple[str, InlineKeyboardMarkup, str | None]:
    """Returns (text, keyboard, photo_url)."""
    user = await user_collection.find_one({"id": user_id})
    if not user or not user.get("characters"):
        return "📭 Your harem is empty — go catch some characters!", InlineKeyboardMarkup([]), None

    chars = user["characters"]
    # Deduplicate keeping all counts
    id_counts: dict[str, int] = {}
    for c in chars:
        id_counts[c["id"]] = id_counts.get(c["id"], 0) + 1

    unique: list[dict] = list({c["id"]: c for c in chars}.values())
    unique.sort(key=lambda x: (x["anime"], x["id"]))

    total_unique = len(unique)
    total_pages  = max(1, math.ceil(total_unique / _PAGE))
    page = max(0, min(page, total_pages - 1))

    page_chars  = unique[page * _PAGE:(page + 1) * _PAGE]
    animes      = list({c["anime"] for c in page_chars})
    db_totals   = await _anime_totals(animes)

    # Header
    fav_id = (user.get("favorites") or [None])[0]
    lines  = [
        f"<b>🌸 {escape(user.get('first_name', 'User'))}'s Harem</b>",
        f"📦 {total_unique} unique  |  🗂 {len(chars)} total  |  "
        f"🟡 {user.get('coins', 0):,} Neko Coins",
        f"Page {page+1}/{total_pages}\n",
    ]

    # Group by anime
    sorted_page = sorted(page_chars, key=lambda x: x["anime"])
    for anime, group_iter in groupby(sorted_page, key=lambda x: x["anime"]):
        group_list = list(group_iter)
        db_total   = db_totals.get(anime, "?")
        lines.append(f"\n<b>{escape(anime)}  {len(group_list)}/{db_total}</b>")
        for c in group_list:
            icon  = _rarity_icon(c.get("rarity", ""))
            cnt   = id_counts.get(c["id"], 1)
            dup   = f" ×{cnt}" if cnt > 1 else ""
            fav   = " ⭐" if c["id"] == fav_id else ""
            lines.append(f"  {icon} <code>{c['id']}</code> {escape(c['name'])}{dup}{fav}")

    text = "\n".join(lines)

    # Keyboard: collection link + navigation + quick-action for fav char
    kb: list[list] = []
    kb.append([InlineKeyboardButton(
        f"🔍 Browse Collection ({len(chars)})",
        switch_inline_query_current_chat=f"collection.{user_id}",
    )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"harem:{page-1}:{user_id}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"harem:{page+1}:{user_id}"))
    if len(nav) > 1:
        kb.append(nav)

    markup = InlineKeyboardMarkup(kb)

    # Stable photo: fav > first unique
    photo: str | None = None
    if fav_id:
        fav_char = next((c for c in chars if c["id"] == fav_id), None)
        photo = _harem_photo_url(fav_char)
    if not photo and page_chars:
        photo = _harem_photo_url(page_chars[0])

    return text, markup, photo


async def _reply_harem(update: Update, text: str,
                       markup: InlineKeyboardMarkup, photo: str | None) -> None:
    is_cb = bool(update.callback_query)
    if not is_cb:
        if photo:
            try:
                await update.message.reply_photo(
                    photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=markup)
                return
            except BadRequest:
                # A broken/unreachable image URL must not make /harem look unresponsive.
                pass
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    query = update.callback_query
    if photo:
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode=ParseMode.HTML),
                reply_markup=markup,
            )
            return
        except BadRequest as e:
            message = str(e).lower()
            if "not modified" in message:
                return
            # If Telegram cannot fetch the new image URL, keep navigation working
            # by updating the existing message's caption or text instead.
            if query.message and query.message.photo:
                await query.edit_message_caption(
                    caption=text, parse_mode=ParseMode.HTML, reply_markup=markup)
            else:
                await query.edit_message_text(
                    text, parse_mode=ParseMode.HTML, reply_markup=markup)
            return

    try:
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def harem(update: Update, context: CallbackContext, page: int = 0) -> None:
    user = update.effective_user
    if user is None:
        return
    user_id = user.id
    try:
        text, markup, photo = await _build_page(user_id, page)
        await _reply_harem(update, text, markup, photo)
    except Exception:
        logger.exception("Harem command failed for user_id=%s page=%s", user_id, page)
        # Always try to send a plain-text response if database, media, or edit
        # operations fail, so the command never silently appears to do nothing.
        message = update.effective_message
        if message is not None:
            try:
                await message.reply_text(
                    "⚠️ Harem could not load right now. Please try again in a moment."
                )
            except Exception:
                logger.exception("Could not send harem fallback response to user_id=%s", user_id)


async def harem_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()
    _, page_str, uid_str = q.data.split(":")
    if q.from_user.id != int(uid_str):
        await q.answer("❌ That's not your harem!", show_alert=True)
        return
    await harem(update, context, page=int(page_str))


async def noop(update: Update, context: CallbackContext) -> None:
    await update.callback_query.answer()


application.add_handler(CommandHandler(["harem", "collection"], harem, block=False))
application.add_handler(CallbackQueryHandler(harem_callback, pattern=r"^harem:\d+:\d+$", block=False))
application.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$", block=False))
