import os
import json

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from waifu import (
    application,
    collection,
    sudo_users,
)

# Channel ID from env
CHANNEL_ID = os.getenv("CHARA_CHANNEL_ID")

if not CHANNEL_ID:
    raise Exception("CHARA_CHANNEL_ID env missing")

CHANNEL_ID = int(CHANNEL_ID)


async def import_json(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in sudo_users:
        return await update.message.reply_text("❌ Not authorized.")

    # must reply to json file
    if not update.message.reply_to_message:
        return await update.message.reply_text(
            "❌ Reply to a JSON file."
        )

    document = update.message.reply_to_message.document

    if not document:
        return await update.message.reply_text(
            "❌ Reply to a JSON file."
        )

    if not document.file_name.endswith(".json"):
        return await update.message.reply_text(
            "❌ Invalid file type."
        )

    await update.message.reply_text(
        "📥 Downloading JSON file..."
    )

    # download json
    file = await context.bot.get_file(document.file_id)

    path = "characters_import.json"

    await file.download_to_drive(path)

    # load json
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    added = 0
    failed = 0

    await update.message.reply_text(
        f"🚀 Importing {len(data)} characters..."
    )

    for char in data:

        try:
            image = char.get("image")
            name = char.get("name")
            anime = char.get("anime")
            rarity = char.get("rarity")

            if not image or not name:
                failed += 1
                continue

            # upload to channel
            msg = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=image,
                caption=name
            )

            # save in mongodb
            await collection.insert_one({
                "img_url": image,
                "name": name,
                "anime": anime,
                "rarity": rarity,
                "message_id": msg.message_id,
                "file_id": msg.photo[-1].file_id
            })

            added += 1

        except Exception as e:
            print(f"Import Error: {e}")
            failed += 1

    await update.message.reply_text(
        f"✅ Import Completed.\n\n"
        f"✔ Imported: {added}\n"
        f"❌ Failed: {failed}"
    )


application.add_handler(
    CommandHandler("importjson", import_json)
            )
