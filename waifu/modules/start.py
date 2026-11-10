import logging
import os
import random
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, BOT_USERNAME, GROUP_ID, PHOTO_URL, SUPPORT_CHAT, UPDATE_CHAT, OWNER_ID
from waifu import pm_users as _pm

MINIAPP_URL = os.environ.get("MINIAPP_URL", "https://nekoverse-miniapp.onrender.com")
LOGGER = logging.getLogger(__name__)


def _welcome(first_name: str, user_id: int) -> str:
    return (
        f"🌸 <b>𝗡𝗘𝗞𝗢𝗩𝗘𝗥𝗦𝗘</b>  💮\n"
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
    "📚 <b>NEKOVERSE COMMAND CENTER</b>\n"
    "<i>Choose a category to explore every command.</i>\n"
    "━━━━━━━━━━━━━━━━━━━━"
)

HELP_CATEGORIES = {
    "game": {
        "title": "🎴 Game & Collection",
        "intro": "Catch drops, build your collection and manage your favourite characters.",
        "commands": [
            {"key": "guess", "label": "🎯 /guess", "usage": "/guess character-name",
             "description": "Guess the name of the character currently dropped in the group. Be quick — the first correct guess claims it.",
             "note": "Use the character's name after the command. Example: /guess Naruto."},
            {"key": "harem", "label": "💗 /harem", "usage": "/harem",
             "description": "Open your collection and browse the characters you have collected. The list is paginated."},
            {"key": "fav", "label": "⭐ /fav", "usage": "/fav character_id",
             "description": "Set one of your collected characters as your favourite. You must own that character ID."},
            {"key": "check", "label": "🔎 /check", "usage": "/check character_id",
             "description": "Look up a character by ID to view its name, anime, rarity and image."},
            {"key": "hclaim", "label": "🎁 /hclaim", "usage": "/hclaim",
             "description": "Claim one free character every 24 hours.",
             "note": "This command only works in the configured claim group."},
            {"key": "profile", "label": "👤 /profile", "usage": "/profile",
             "description": "View your player profile, level, XP, Neko Coins, Neko Gems, collection size and rarity breakdown."},
            {"key": "book", "label": "📚 /book", "usage": "/book  |  /book anime-name",
             "description": "Track your completion progress by anime and see which characters you still need to collect."},
            {"key": "wishlist", "label": "⭐ /wish", "usage": "/wish character-name  |  /wishlist  |  /unwish character-name",
             "description": "Save up to 20 characters and receive a private alert when one appears in a group drop."},
            {"key": "achievements", "label": "🏅 /achievements", "usage": "/achievements",
             "description": "View your unlocked milestones for collecting, rarity hunting, duels, streaks and mini-games."},
            {"key": "card", "label": "🎴 /card", "usage": "/card character_id",
             "description": "Create a holographic MikasaVerse collector card for a character you own or a catalogue character."},
        ],
    },
    "economy": {
        "title": "💰 Economy",
        "intro": "Earn 🟡 Neko Coins, collect 💎 Neko Gems, manage your balance and use the character marketplace.",
        "commands": [
            {"key": "daily", "label": "🟡 /daily", "usage": "/daily",
             "description": "Collect your daily 🟡 Neko Coins reward and build a streak for extra bonus coins."},
            {"key": "weekly", "label": "🌟 /weekly", "usage": "/weekly",
             "description": "Claim a weekly Neko Coin bonus every 7 days."},
            {"key": "summon", "label": "🎴 /summon", "usage": "/summon  |  /summon 10",
             "description": "Spend Neko Coins on weighted character summons. A Rare-or-higher pull is guaranteed by the 10th consecutive pull."},
            {"key": "balance", "label": "💳 /balance", "usage": "/balance",
             "description": "Check your current in-game balance."},
            {"key": "pay", "label": "💸 /pay", "usage": "Reply to a player: /pay amount",
             "description": "Transfer Neko Coins directly to another player."},
            {"key": "exchange", "label": "💱 /exchange", "usage": "/exchange coins 1000",
             "description": "Exchange 1,000 🟡 Neko Coins for 1 💎 Neko Gem. You can also exchange gems back with /exgem."},
            {"key": "exgem", "label": "💎 /exgem", "usage": "/exgem 1",
             "description": "Exchange 1 💎 Neko Gem for 900 🟡 Neko Coins."},
            {"key": "market", "label": "🛒 /market", "usage": "/market",
             "description": "Browse character listings available for purchase in the marketplace."},
            {"key": "sell", "label": "🏷️ /sell", "usage": "/sell character_id price",
             "description": "Put one of your characters up for sale. Enter the character ID and the price you want."},
            {"key": "buy", "label": "🛍️ /buy", "usage": "/buy listing_id",
             "description": "Purchase a marketplace listing using its listing ID. You need enough 🟡 Neko Coins."},
        ],
    },
    "games": {
        "title": "🎮 Games",
        "intro": "Play group mini-games, test your anime knowledge and compete with others.",
        "commands": [
            {"key": "quiz", "label": "🧠 /quiz", "usage": "/quiz easy  |  /quiz medium  |  /quiz hard",
             "description": "Start an anime character image quiz. Choose a difficulty and answer the multiple-choice rounds.",
             "note": "Only works in the configured quiz group."},
            {"key": "trivia", "label": "🧩 /trivia", "usage": "/trivia",
             "description": "Start a timed multiple-choice round: identify which anime a character belongs to. Correct answers earn coins and XP."},
            {"key": "nguess", "label": "🎯 /nguess", "usage": "/nguess",
             "description": "Start the name-guessing game. Type the character's name when each image appears.",
             "note": "Only works in the configured game group."},
            {"key": "tower", "label": "🏯 /tower", "usage": "/tower",
             "description": "Climb the Infinite Tower by solving increasingly difficult maths challenges. You have 3 lives and earn 🟡 Neko Coins plus XP for every correct floor.",
             "note": "Solo game. A new run has a 5-minute cooldown."},
            {"key": "tower_stop", "label": "🛑 /tower_stop", "usage": "/tower_stop",
             "description": "Stop your current Infinite Tower run. Rewards from floors you already cleared stay in your balance."},
            {"key": "scramble", "label": "🧩 /scramble", "usage": "/scramble",
             "description": "Unscramble a character name from the catalogue. The first correct answer in the chat earns 35 🟡 Neko Coins and 12 XP.",
             "note": "Chat-wide round with a 60-second timer."},
            {"key": "scramble_stop", "label": "🛑 /scramble_stop", "usage": "/scramble_stop",
             "description": "Stop the active Word Scramble round. Only the person who started the round can stop it."},
            {"key": "bet", "label": "🎲 /bet", "usage": "/bet amount",
             "description": "Play a virtual Neko Coin dice challenge. Stakes are limited and no real money is involved."},
            {"key": "dart", "label": "🎯 /dart", "usage": "/dart amount",
             "description": "Try to hit the bullseye in Telegram's animated dart game using Neko Coins."},
            {"key": "bowl", "label": "🎳 /bowl", "usage": "/bowl amount",
             "description": "Play the animated bowling challenge with a virtual coin stake."},
            {"key": "basket", "label": "🏀 /basket", "usage": "/basket amount",
             "description": "Play the animated basketball challenge with a virtual coin stake."},
            {"key": "ship", "label": "💘 /ship", "usage": "Reply to a user with /ship",
             "description": "Calculate a playful daily compatibility score between two players and show their combined collection."},
        ],
    },
    "music": {
        "title": "🎵 Voice Chat Music",
        "intro": "Play songs and YouTube playlists in your group's voice chat, with interactive controls.",
        "commands": [
            {"key": "play", "label": "🎵 /play", "usage": "/play song name, YouTube link, or playlist link",
             "description": "Play a song or queue up to 20 tracks from a YouTube playlist.",
             "note": "Requires the separate music assistant account to be configured. The main bot needs Invite Users permission; the assistant does not need admin rights."},
            {"key": "pause", "label": "⏸️ /pause", "usage": "/pause",
             "description": "Pause the current voice-chat track."},
            {"key": "resume", "label": "▶️ /resume", "usage": "/resume",
             "description": "Resume the paused track."},
            {"key": "skip", "label": "⏭️ /skip", "usage": "/skip",
             "description": "Skip the current song and play the next queued track."},
            {"key": "current", "label": "🎧 /current", "usage": "/current",
             "description": "Show the current track and interactive player controls."},
            {"key": "replay", "label": "🔁 /replay", "usage": "/replay",
             "description": "Restart the current track without clearing the queue."},
            {"key": "loop", "label": "🔂 /loop", "usage": "/loop 0-10",
             "description": "Repeat the current track up to 10 additional times; use 0 to disable."},
            {"key": "shuffle", "label": "🔀 /shuffle", "usage": "/shuffle",
             "description": "Shuffle upcoming tracks in the queue."},
            {"key": "clearqueue", "label": "🧹 /clearqueue", "usage": "/clearqueue",
             "description": "Clear upcoming tracks while the current song continues."},
            {"key": "end", "label": "⏹️ /end", "usage": "/end",
             "description": "Stop playback, clear the queue and leave the voice chat."},
            {"key": "queue", "label": "📃 /queue", "usage": "/queue",
             "description": "Show the current track and upcoming songs."},
            {"key": "volume", "label": "🔊 /volume", "usage": "/volume 1-200",
             "description": "Change the music volume from 1% to 200%."},
            {"key": "song", "label": "⬇️ /song", "usage": "/song song name or link",
             "description": "Download a searched track as an MP3 audio file and send it in the chat. Anonymous YouTube restrictions may apply."},
            {"key": "favorite", "label": "❤️ /favorite", "usage": "/favorite",
             "description": "Save the currently playing track to your personal MongoDB favourites."},
            {"key": "favs", "label": "💖 /favs", "usage": "/favs",
             "description": "Browse your saved music favourites."},
            {"key": "playfav", "label": "▶️ /playfav", "usage": "/playfav number",
             "description": "Add one of your saved favourite tracks to the voice-chat queue."},
            {"key": "delfav", "label": "🗑️ /delfav", "usage": "/delfav number",
             "description": "Remove one saved favourite from your list."},
        ],
    },
    "social": {
        "title": "⚔️ Social",
        "intro": "Interact with other players through marriage, trading, gifting and duels.",
        "commands": [
            {"key": "marry", "label": "💍 /marry", "usage": "Reply to a user with /marry",
             "description": "Send a marriage proposal to the user whose message you replied to."},
            {"key": "divorce", "label": "💔 /divorce", "usage": "/divorce",
             "description": "End your current in-game marriage."},
            {"key": "couple", "label": "💕 /couple", "usage": "/couple",
             "description": "Reveal the group's Couple of the Day, selected from players active in that group.",
             "note": "Works in groups, not in private chat."},
            {"key": "trade", "label": "🔄 /trade", "usage": "Reply to a user: /trade YOUR_ID THEIR_ID",
             "description": "Propose swapping one of your characters for one owned by another player. The other player can accept or reject the offer."},
            {"key": "gift", "label": "🎁 /gift", "usage": "Reply to a user: /gift CHARACTER_ID",
             "description": "Offer one of your collected characters as a gift to the user you replied to."},
            {"key": "duel", "label": "⚡ /duel", "usage": "Reply to a user with /duel",
             "description": "Challenge another player to a character duel. Both players choose a character and the bot calculates the result."},
        ],
    },
    "leaderboards": {
        "title": "🏆 Leaderboards",
        "intro": "See who is collecting the most characters.",
        "commands": [
            {"key": "top", "label": "🥇 /top", "usage": "/top",
             "description": "Show the top 10 collectors across the bot."},
            {"key": "ctop", "label": "🏅 /ctop", "usage": "/ctop",
             "description": "Show the top collectors in the current group."},
            {"key": "topgroups", "label": "🌐 /TopGroups", "usage": "/TopGroups",
             "description": "Show the most active groups leaderboard."},
        ],
    },
    "settings": {
        "title": "⚙️ Group Settings",
        "intro": "Group admins can control how often character drops appear.",
        "commands": [
            {"key": "changetime", "label": "⏱️ /changetime", "usage": "/changetime number",
             "description": "Set how many group messages are needed before the next character drop.",
             "note": "Group admins only. Allowed values: 5 to 10,000."},
            {"key": "resettime", "label": "♻️ /resettime", "usage": "/resettime",
             "description": "Reset the group's drop frequency to the bot's default setting.",
             "note": "Group admins only. Use this in a group."},
        ],
    },
}


def _help_home_keyboard() -> InlineKeyboardMarkup:
    rows = []
    category_items = list(HELP_CATEGORIES.items())
    for index in range(0, len(category_items), 2):
        row = []
        for key, category in category_items[index:index + 2]:
            row.append(InlineKeyboardButton(
                category["title"], callback_data=f"help:category:{key}"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Back to Welcome", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def _help_category_keyboard(category_key: str) -> InlineKeyboardMarkup:
    category = HELP_CATEGORIES[category_key]
    commands = category["commands"]
    rows = []
    for index in range(0, len(commands), 2):
        row = []
        for command in commands[index:index + 2]:
            prefix = "" if command.get("available", True) else "🚧 "
            row.append(InlineKeyboardButton(
                f"{prefix}{command['label']}",
                callback_data=f"help:command:{category_key}:{command['key']}",
            ))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("⬅️ Categories", callback_data="help:home"),
        InlineKeyboardButton("🏠 Welcome", callback_data="back"),
    ])
    return InlineKeyboardMarkup(rows)


def _help_command_keyboard(category_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "⬅️ Back to " + HELP_CATEGORIES[category_key]["title"],
            callback_data=f"help:category:{category_key}",
        )],
        [InlineKeyboardButton("📚 All Help Categories", callback_data="help:home")],
    ])


def _help_category_text(category_key: str) -> str:
    category = HELP_CATEGORIES[category_key]
    return (
        f"<b>{category['title']}</b>\n"
        f"<i>{category['intro']}</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Select a command to see what it does and how to use it."
    )


def _help_command_text(category_key: str, command_key: str) -> str:
    category = HELP_CATEGORIES[category_key]
    command = next(
        (item for item in category["commands"] if item["key"] == command_key),
        None,
    )
    if command is None:
        return "❌ This help option could not be found."

    status = ""
    if not command.get("available", True):
        status = (
            "\n\n🚧 <b>Not active in this bot version</b>\n"
            + command.get("note", "This command is not available yet.")
        )
    elif command.get("note"):
        status = f"\n\nℹ️ <i>{command['note']}</i>"

    return (
        f"📘 <b>{command['label']}</b>\n\n"
        f"{command['description']}\n\n"
        f"⌨️ <b>Usage:</b> <code>{command['usage']}</code>"
        f"{status}"
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
    """Edit the message containing the menu, or send a replacement if editing fails."""
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
        LOGGER.exception("Could not edit Help message; sending a replacement.")

    try:
        if q.message:
            await q.message.reply_text(
                text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
    except Exception:
        LOGGER.exception("Could not send replacement Help message.")


async def button(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        LOGGER.debug("Callback query could not be acknowledged.", exc_info=True)

    user = q.from_user
    data = q.data or ""

    if data in ("help", "help:home"):
        await _edit_or_send(q, HELP, _help_home_keyboard())
        return

    if data.startswith("help:category:"):
        category_key = data.split(":")[-1]
        if category_key not in HELP_CATEGORIES:
            await q.answer("This help category was not found.", show_alert=True)
            return
        category = HELP_CATEGORIES[category_key]
        await _edit_or_send(
            q,
            _help_category_text(category_key),
            _help_category_keyboard(category_key),
        )
        return

    if data.startswith("help:command:"):
        parts = data.split(":")
        if len(parts) != 4:
            await q.answer("Invalid help option.", show_alert=True)
            return
        _, _, category_key, command_key = parts
        if category_key not in HELP_CATEGORIES:
            await q.answer("This help category was not found.", show_alert=True)
            return
        command_exists = any(
            item["key"] == command_key
            for item in HELP_CATEGORIES[category_key]["commands"]
        )
        if not command_exists:
            await q.answer("This command help was not found.", show_alert=True)
            return
        await _edit_or_send(
            q,
            _help_command_text(category_key, command_key),
            _help_command_keyboard(category_key),
        )
        return

    if data == "owner":
        owner_text = (
            "👑 <b>BOT OWNER</b>\n\n"
            f"<a href='tg://user?id={OWNER_ID}'>Tap here to contact the owner</a>"
        )
        await _edit_or_send(
            q, owner_text,
            InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back to Welcome", callback_data="back")
            ]]),
        )
        return

    if data == "back":
        welcome_kb = _kb(
            include_miniapp=q.message.chat.type == "private",
            include_admin=user.id == OWNER_ID and q.message.chat.type == "private",
        )
        await _edit_or_send(q, _welcome(user.first_name, user.id), welcome_kb)


async def help_command(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(
        HELP,
        reply_markup=_help_home_keyboard(),
        parse_mode=ParseMode.HTML,
    )

application.add_handler(CommandHandler("start", start, block=False))
application.add_handler(CommandHandler("help", help_command, block=False))
# Help navigation runs before other callback handlers so every menu button responds.
application.add_handler(
    CallbackQueryHandler(
        button,
        pattern=r"^(help(?::.*)?|back|owner)$",
        block=False,
    ),
    group=-1,
)
            
