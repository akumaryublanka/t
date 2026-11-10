"""Anime Collection Book — per-series owned and missing character progress."""
import math
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, collection, user_collection

_PAGE_SIZE = 10


async def _book_data(user_id: int):
    player = await user_collection.find_one({"id": user_id}) or {}
    owned = {str(c.get("id")) for c in player.get("characters", [])}
    catalogue = await collection.find({}).to_list(length=10000)
    grouped: dict[str, list[dict]] = {}
    for char in catalogue:
        anime = str(char.get("anime") or "Unknown")
        grouped.setdefault(anime, []).append(char)
    rows = []
    for anime, chars in grouped.items():
        unique = {str(c.get("id")): c for c in chars}
        owned_chars = [c for cid, c in unique.items() if cid in owned]
        rows.append({
            "anime": anime,
            "total": len(unique),
            "owned": len(owned_chars),
            "missing": [c for cid, c in unique.items() if cid not in owned],
        })
    rows.sort(key=lambda x: (x["owned"] / max(x["total"], 1), x["anime"].casefold()), reverse=True)
    return player, rows


def _book_keyboard(user_id: int, page: int, pages: int):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️", callback_data=f"book:{user_id}:{page-1}"))
    row.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="book:noop"))
    if page + 1 < pages:
        row.append(InlineKeyboardButton("➡️", callback_data=f"book:{user_id}:{page+1}"))
    return InlineKeyboardMarkup([row]) if len(row) > 1 else InlineKeyboardMarkup([])


async def _render_book(user_id: int, page: int = 0, anime_query: str = ""):
    player, rows = await _book_data(user_id)
    if anime_query:
        needle = anime_query.casefold()
        matches = [r for r in rows if needle in r["anime"].casefold()]
        if not matches:
            return "❌ No anime title matched that search.", InlineKeyboardMarkup([])
        lines = ["📚 <b>ANIME COLLECTION BOOK</b>", "━━━━━━━━━━━━━━━━━━"]
        for item in matches[:8]:
            pct = round(item["owned"] * 100 / max(item["total"], 1))
            lines.append(f"\n🎬 <b>{escape(item['anime'])}</b>  ·  {item['owned']}/{item['total']} ({pct}%)")
            if len(matches) == 1:
                missing = item["missing"][:12]
                if missing:
                    lines.append("🔍 <i>Still missing:</i>")
                    lines.extend(f"  • {escape(str(c.get('name', 'Unknown')))} <code>{escape(str(c.get('id', '')))}</code>" for c in missing)
                else:
                    lines.append("🏆 <b>Complete collection!</b>")
        if len(matches) > 1:
            lines.append("\nAdd more of the anime title to see its missing characters.")
        return "\n".join(lines), InlineKeyboardMarkup([])

    pages = max(1, math.ceil(len(rows) / _PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    selected = rows[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]
    unique_count = len({str(c.get("id")) for c in player.get("characters", [])})
    overall = sum(x["owned"] for x in rows)
    total = sum(x["total"] for x in rows)
    lines = [
        "📚 <b>MIKASAVERSE — COLLECTION BOOK</b>",
        f"👤 {escape(player.get('first_name', 'Collector'))}  ·  🎴 {unique_count} unique characters",
        f"🌸 Overall catalogue: <b>{overall}/{total}</b> ({round(overall*100/max(total,1))}%)",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for item in selected:
        pct = round(item["owned"] * 100 / max(item["total"], 1))
        filled = round(pct / 10)
        bar = "🟪" * filled + "⬛" * (10 - filled)
        medal = " ✅" if item["owned"] == item["total"] and item["total"] else ""
        lines.append(f"\n🎬 <b>{escape(item['anime'])}</b>{medal}\n{bar}  {item['owned']}/{item['total']} · {pct}%")
    lines.append("\nUse <code>/book anime-name</code> to see missing characters.")
    return "\n".join(lines), _book_keyboard(user_id, page, pages)


async def collection_book(update: Update, context: CallbackContext) -> None:
    args = context.args or []
    page = 0
    query = ""
    if args and args[0].isdigit():
        page = max(0, int(args[0]) - 1)
    elif args:
        query = " ".join(args)
    text, keyboard = await _render_book(update.effective_user.id, page, query)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def book_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await query.answer()
        return
    owner_id, page = int(parts[1]), int(parts[2])
    if query.from_user.id != owner_id:
        await query.answer("This is someone else's collection book.", show_alert=True)
        return
    text, keyboard = await _render_book(owner_id, page)
    await query.answer()
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


application.add_handler(CommandHandler(["book", "collectionbook"], collection_book, block=False))
application.add_handler(CallbackQueryHandler(book_callback, pattern=r"^book:\d+:\d+$", block=False))
application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern=r"^book:noop$", block=False))
