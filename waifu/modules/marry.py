"""
modules/marry.py

/marry   — Propose to a user (reply to their message)
/divorce — End your marriage
/spouse  — Check who you're married to
"""
import asyncio
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection

# Pending proposals: {proposer_id: target_id}
_pending: dict[int, int] = {}

PROPOSAL_TIMEOUT = 60  # seconds


async def marry(update: Update, context: CallbackContext) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "💍 Reply to someone's message to propose!\n"
            "Usage: Reply + <code>/marry</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    proposer = update.effective_user
    target   = update.message.reply_to_message.from_user

    if target.id == proposer.id:
        await update.message.reply_text("💔 You can't marry yourself... find your soulmate!")
        return

    if target.is_bot:
        await update.message.reply_text("🤖 Bots can't get married!")
        return

    # Check if proposer already married
    proposer_doc = await user_collection.find_one({"id": proposer.id})
    if proposer_doc and proposer_doc.get("spouse_id"):
        spouse_name = proposer_doc.get("spouse_name", "someone")
        await update.message.reply_text(
            f"💍 You're already married to <b>{escape(spouse_name)}</b>!\n"
            f"Use /divorce first.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Check if target already married
    target_doc = await user_collection.find_one({"id": target.id})
    if target_doc and target_doc.get("spouse_id"):
        spouse_name = target_doc.get("spouse_name", "someone")
        await update.message.reply_text(
            f"💔 <b>{escape(target.first_name)}</b> is already married to <b>{escape(spouse_name)}</b>!",
            parse_mode=ParseMode.HTML,
        )
        return

    # Check if already pending
    if proposer.id in _pending:
        await update.message.reply_text("⏳ You already have a pending proposal! Wait for a response.")
        return

    _pending[proposer.id] = target.id

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💍 Accept", callback_data=f"marry_accept:{proposer.id}:{target.id}"),
        InlineKeyboardButton("💔 Decline", callback_data=f"marry_decline:{proposer.id}:{target.id}"),
    ]])

    await update.message.reply_text(
        f"💌 <a href='tg://user?id={proposer.id}'>{escape(proposer.first_name)}</a> "
        f"is proposing to "
        f"<a href='tg://user?id={target.id}'>{escape(target.first_name)}</a>!\n\n"
        f"💍 <i>Will you accept this proposal?</i>\n\n"
        f"⏳ You have {PROPOSAL_TIMEOUT} seconds to decide!",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )

    # Auto-expire proposal
    await asyncio.sleep(PROPOSAL_TIMEOUT)
    if proposer.id in _pending:
        _pending.pop(proposer.id, None)
        try:
            await update.message.reply_text(
                f"⌛ The proposal from <b>{escape(proposer.first_name)}</b> has expired.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def marry_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    data = q.data.split(":")
    action      = data[0]
    proposer_id = int(data[1])
    target_id   = int(data[2])

    # Only target can respond
    if q.from_user.id != target_id:
        await q.answer("❌ This proposal isn't for you!", show_alert=True)
        return

    # Check proposal still pending
    if _pending.get(proposer_id) != target_id:
        await q.edit_message_text("⌛ This proposal has already expired.")
        return

    _pending.pop(proposer_id, None)

    if action == "marry_decline":
        await q.edit_message_text(
            f"💔 The proposal was declined...\n"
            f"<i>Maybe next time!</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Accept — get names
    proposer_doc = await user_collection.find_one({"id": proposer_id})
    target_doc   = await user_collection.find_one({"id": target_id})

    proposer_name = proposer_doc.get("first_name", "Unknown") if proposer_doc else "Unknown"
    target_name   = q.from_user.first_name

    # Double check not already married (race condition)
    if (proposer_doc and proposer_doc.get("spouse_id")) or \
       (target_doc   and target_doc.get("spouse_id")):
        await q.edit_message_text("💔 Someone got married in the meantime!")
        return

    # Save marriage to DB
    await user_collection.update_one(
        {"id": proposer_id},
        {"$set": {"spouse_id": target_id, "spouse_name": target_name}},
        upsert=True,
    )
    await user_collection.update_one(
        {"id": target_id},
        {"$set": {"spouse_id": proposer_id, "spouse_name": proposer_name}},
        upsert=True,
    )

    await q.edit_message_text(
        f"💒 <b>Congratulations!</b>\n\n"
        f"💍 <a href='tg://user?id={proposer_id}'>{escape(proposer_name)}</a> "
        f"& "
        f"<a href='tg://user?id={target_id}'>{escape(target_name)}</a> "
        f"are now married! 🎉\n\n"
        f"🌸 <i>May your harem grow together!</i> 🌸",
        parse_mode=ParseMode.HTML,
    )


async def divorce(update: Update, context: CallbackContext) -> None:
    user_id  = update.effective_user.id
    user_doc = await user_collection.find_one({"id": user_id})

    if not user_doc or not user_doc.get("spouse_id"):
        await update.message.reply_text("💔 You're not married to anyone!")
        return

    spouse_id   = user_doc["spouse_id"]
    spouse_name = user_doc.get("spouse_name", "Unknown")

    # Remove marriage from both
    await user_collection.update_one(
        {"id": user_id},
        {"$unset": {"spouse_id": "", "spouse_name": ""}},
    )
    await user_collection.update_one(
        {"id": spouse_id},
        {"$unset": {"spouse_id": "", "spouse_name": ""}},
    )

    await update.message.reply_text(
        f"💔 You have divorced <b>{escape(spouse_name)}</b>.\n"
        f"<i>Another waifu awaits...</i>",
        parse_mode=ParseMode.HTML,
    )


async def spouse(update: Update, context: CallbackContext) -> None:
    user_id  = update.effective_user.id
    user_doc = await user_collection.find_one({"id": user_id})

    if not user_doc or not user_doc.get("spouse_id"):
        await update.message.reply_text(
            "💔 You're not married to anyone yet!\n"
            "Reply to someone's message and use /marry 💍"
        )
        return

    spouse_id   = user_doc["spouse_id"]
    spouse_name = user_doc.get("spouse_name", "Unknown")

    await update.message.reply_text(
        f"💍 You are married to "
        f"<a href='tg://user?id={spouse_id}'>{escape(spouse_name)}</a>!\n\n"
        f"🌸 <i>A beautiful waifu bond!</i>",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("marry",   marry,   block=False))
application.add_handler(CommandHandler("divorce", divorce, block=False))
application.add_handler(CommandHandler("spouse",  spouse,  block=False))
application.add_handler(CallbackQueryHandler(
    marry_callback,
    pattern=r"^marry_(accept|decline):\d+:\d+$",
    block=False,
))
  
