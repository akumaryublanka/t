"""
maintenance.py — Silent Maintenance Mode (OWNER ONLY)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• /maintenance on  → Bot completely silent, no replies
• /maintenance off → Bot normal ho jaata hai
• OWNER_ID aur SUDO_IDS admins use kar sakte hain
• State MongoDB mein save hota hai — restart pe bhi yaad rehta hai
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from telegram.constants import ParseMode

from waifu import application, db, OWNER_ID, sudo_users

# Owner and SUDO_IDS admins can continue using the bot during maintenance.
MAINTENANCE_ADMINS = set(sudo_users) | {OWNER_ID}

# MongoDB
settings_col = db["bot_settings"]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def is_maintenance() -> bool:
    doc = await settings_col.find_one({"_id": "maintenance"})
    return bool(doc and doc.get("enabled", False))


async def set_maintenance(state: bool):
    await settings_col.update_one(
        {"_id": "maintenance"},
        {"$set": {"enabled": state}},
        upsert=True
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAINTENANCE COMMAND
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or user.id not in MAINTENANCE_ADMINS:
        return

    args = context.args
    if not args or args[0].lower() not in ("on", "off"):
        await message.reply_text(
            "Usage:\n/maintenance on\n/maintenance off",
            parse_mode=ParseMode.HTML
        )
        return

    state = args[0].lower() == "on"
    await set_maintenance(state)

    if state:
        await message.reply_text(
            "🔴 <b>Maintenance Mode ON</b>\n\nNormal users ke liye bot temporarily paused hai.\nOwner aur SUDO_IDS admins commands use kar sakte hain.",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            "🟢 <b>Maintenance Mode OFF</b>\n\nBot wapas normal ho gaya!\nSab commands ab kaam karenge. ✅",
            parse_mode=ParseMode.HTML
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GLOBAL BLOCK HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def block_during_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Owner and configured SUDO_IDS admins remain allowed during maintenance.
    if user and user.id in MAINTENANCE_ADMINS:
        return

    # Maintenance on hai toh sab block
    if await is_maintenance():
        raise ApplicationHandlerStop

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DIRECT REGISTRATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from telegram.ext import ApplicationHandlerStop

application.add_handler(
    MessageHandler(filters.ALL, block_during_maintenance),
    group=-1
)

application.add_handler(
    CommandHandler("maintenance", maintenance_command)
)
