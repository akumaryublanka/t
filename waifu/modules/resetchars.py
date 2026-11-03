from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from waifu import application
from waifu import collection, sudo_users


async def reset_chars(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in sudo_users:
        return await update.message.reply_text("❌ Not authorized.")

    total = await collection.count_documents({})

    await collection.delete_many({})

    await update.message.reply_text(
        f"✅ Deleted {total} characters from database."
    )


application.add_handler(CommandHandler("resetchars", reset_chars))
