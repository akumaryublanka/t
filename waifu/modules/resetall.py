"""
modules/resetall.py

/resetall — Owner only. Resets ALL user data permanently.
Characters DB (anime_characters) is NOT touched.
"""
import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import (
    application, OWNER_ID,
    user_collection,
    group_user_totals_collection,
    top_global_groups_collection,
    user_totals_collection,
    db,
)

# Pending confirmations
_pending: set[int] = set()


async def resetall(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id

    if user_id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return

    _pending.add(user_id)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ YES, Reset Everything", callback_data="resetall_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="resetall_cancel"),
    ]])

    await update.message.reply_text(
        "⚠️ <b>WARNING! PERMANENT ACTION!</b>\n\n"
        "This will delete:\n"
        "❌ All users' harems\n"
        "❌ All coins & XP\n"
        "❌ All leaderboards\n"
        "❌ All group stats\n"
        "❌ All market listings\n\n"
        "✅ <b>Characters DB will be SAFE</b>\n\n"
        "<b>Are you absolutely sure?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def resetall_callback(update: Update, context: CallbackContext) -> None:
    q       = update.callback_query
    user_id = q.from_user.id

    await q.answer()

    if user_id != OWNER_ID:
        await q.answer("❌ Owner only!", show_alert=True)
        return

    if q.data == "resetall_cancel":
        _pending.discard(user_id)
        await q.edit_message_text("✅ Reset cancelled. Nothing was changed.")
        return

    if q.data == "resetall_confirm":
        if user_id not in _pending:
            await q.edit_message_text("❌ Session expired. Run /resetall again.")
            return

        _pending.discard(user_id)

        await q.edit_message_text(
            "⏳ <b>Resetting all data...</b>",
            parse_mode=ParseMode.HTML,
        )

        try:
            # Reset user collection — keep id, username, first_name only
            await user_collection.update_many(
                {},
                {
                    "$set": {
                        "characters":    [],
                        "coins":         0,
                        "xp":            0,
                        "wins":          0,
                        "total_guesses": 0,
                        "favorites":     [],
                        "last_daily":    0,
                        "last_weekly":   0,
                        "last_hclaim":   0,
                        "spouse_id":     None,
                        "spouse_name":   None,
                    }
                }
            )

            # Clear leaderboard collections
            await group_user_totals_collection.delete_many({})
            await top_global_groups_collection.delete_many({})

            # Clear market listings
            await db["market_listings"].delete_many({})

            # Clear quiz scores
            await db["quiz_scores"].delete_many({})

            # Clear redeem used_by lists
            await db["redeem_codes"].update_many(
                {},
                {"$set": {"used_by": [], "uses_left": 0}},
            )

            await q.edit_message_text(
                "✅ <b>Reset Complete!</b>\n\n"
                "❌ All harems cleared\n"
                "❌ All coins & XP reset\n"
                "❌ All leaderboards cleared\n"
                "❌ All market listings removed\n\n"
                "✅ <b>Characters DB is safe!</b>\n\n"
                "<i>Bot is ready for a fresh start! 🌸</i>",
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:
            await q.edit_message_text(
                f"❌ <b>Reset failed!</b>\n\nError: {e}",
                parse_mode=ParseMode.HTML,
            )


application.add_handler(CommandHandler("resetall", resetall, block=False))
application.add_handler(CallbackQueryHandler(
    resetall_callback,
    pattern=r"^resetall_(confirm|cancel)$",
    block=False,
))
