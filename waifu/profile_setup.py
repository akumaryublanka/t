"""Set the public Telegram profile text and command menu at startup.

The Description Picture is not exposed by the standard Telegram Bot API.
Upload the generated MikasaVerse banner once via @BotFather.
"""
from telegram import BotCommand

from waifu import LOGGER


ABOUT_TEXT = (
    "🎴 Catch waifus & husbandos, collect rare characters, trade and compete!"
)

DESCRIPTION_TEXT = (
    "🌸 Welcome to MikasaVerse!\n\n"
    "🎴 Catch anime characters as they drop in your group. Build your collection, "
    "hunt rare editions, earn Neko Coins & Gems, trade or gift characters, and "
    "challenge friends in PvP duels.\n\n"
    "🎮 Play Quiz, Trivia, Mines, Tower, Scramble and dice mini-games. "
    "Summon characters, earn streak rewards, build your wishlist, and create holographic cards.\n\n"
    "⚔️ Collect. Trade. Compete. Become a legend!"
)

# Keep this list aligned with the player-facing handlers in waifu/modules/.
# Owner-only maintenance/developer commands are intentionally not advertised.
# Telegram renders one flat command list, so the descriptions use a
# consistent emoji + short action format and are ordered by gameplay category.
# Keep every entry synced with a real, player-facing CommandHandler.
BOT_COMMANDS = [
    # 🌸 Start here
    BotCommand("start", "🌸 Begin your MikasaVerse journey"),
    BotCommand("help", "📖 Explore commands and game guides"),

    # 🎴 Collection & progression
    BotCommand("guess", "🎴 Catch the character that just dropped"),
    BotCommand("harem", "💗 Browse your waifu & husbando collection"),
    BotCommand("hclaim", "🎁 Claim a free character when available"),
    BotCommand("fav", "⭐ Set your favourite character"),
    BotCommand("profile", "👤 View your level, XP and player stats"),
    BotCommand("book", "📚 Track anime collection completion"),
    BotCommand("wish", "⭐ Add a character to your wishlist"),
    BotCommand("wishlist", "🔔 View your wishlist"),
    BotCommand("unwish", "➖ Remove a character from your wishlist"),
    BotCommand("card", "🎴 Create a holographic character card"),
    BotCommand("achievements", "🏅 View your unlocked milestones"),
    BotCommand("rarity", "💎 Explore your collection's rarity"),

    # 🏆 Leaderboards
    BotCommand("top", "🏆 Global collector leaderboard"),
    BotCommand("ctop", "🥇 Current group collector leaderboard"),

    # 🪙 Neko economy & marketplace
    BotCommand("daily", "🪙 Collect your daily Neko Coins"),
    BotCommand("weekly", "🌟 Collect your weekly Neko Coins"),
    BotCommand("summon", "🎴 Summon characters with Neko Coins"),
    BotCommand("pay", "💸 Transfer Neko Coins to a player"),
    BotCommand("balance", "💰 Check your Coins and Gems"),
    BotCommand("exchange", "🔄 Exchange Coins for Gems"),
    BotCommand("exgem", "💠 Convert Gems back into Coins"),
    BotCommand("market", "🛍️ Explore the character marketplace"),
    BotCommand("buy", "🛒 Buy a character from the market"),
    BotCommand("sell", "🏷️ List one of your characters for sale"),
    BotCommand("delist", "📦 Remove your character listing"),
    BotCommand("redeem", "🎟️ Redeem a reward code"),
    BotCommand("donate", "💖 Support the MikasaVerse project"),

    # 🤝 Social & relationships
    BotCommand("couple", "🌹 Discover today's group couple"),
    BotCommand("ship", "💘 Check player compatibility (reply)"),
    BotCommand("duel", "⚔️ Challenge a player to a character duel"),
    BotCommand("trade", "🤝 Trade characters with another player"),
    BotCommand("gift", "🎀 Send a character to a friend"),
    BotCommand("marry", "💍 Propose an in-game marriage"),
    BotCommand("spouse", "💕 View your in-game partner"),
    BotCommand("divorce", "💔 End your in-game marriage"),

    # 🎮 Mini-games
    BotCommand("quiz", "🧩 Guess the anime character from an image"),
    BotCommand("trivia", "🧠 Play timed anime trivia"),
    BotCommand("bet", "🎲 Play a virtual coin dice challenge"),
    BotCommand("dart", "🎯 Try the virtual dart challenge"),
    BotCommand("bowl", "🎳 Play virtual bowling"),
    BotCommand("basket", "🏀 Play virtual basketball"),
    BotCommand("qlb", "🎯 View the anime quiz leaderboard"),
    BotCommand("mines", "💣 Play the Mines challenge"),
    BotCommand("cashout", "💵 Collect your Mines winnings"),
    BotCommand("nguess", "🔎 Start a character-name challenge"),
    BotCommand("nguess_stop", "⏹️ Stop the name-guessing round"),
    BotCommand("tower", "🗼 Climb the Infinite Tower"),
    BotCommand("tower_stop", "🛑 Stop your Tower run"),
    BotCommand("scramble", "🔤 Unscramble an anime character name"),
    BotCommand("scramble_stop", "⏹️ Stop the scramble round"),

    # 🎵 Voice chat music
    BotCommand("play", "🎵 Play or queue a song in voice chat"),
    BotCommand("pause", "⏸️ Pause voice chat music"),
    BotCommand("resume", "▶️ Resume voice chat music"),
    BotCommand("skip", "⏭️ Skip the current track"),
    BotCommand("end", "⏹️ Stop music and clear the queue"),
    BotCommand("queue", "📃 View the music queue"),
    BotCommand("current", "🎧 Show the current track"),
    BotCommand("replay", "🔁 Replay the current track"),
    BotCommand("loop", "🔂 Repeat the current track 0-10 times"),
    BotCommand("shuffle", "🔀 Shuffle upcoming tracks"),
    BotCommand("clearqueue", "🧹 Clear upcoming tracks"),
    BotCommand("volume", "🔊 Change music volume"),
]


async def setup_bot_profile(bot) -> None:
    """Apply profile text and the command menu using Telegram's Bot API."""
    updates = (
        (
            "About",
            bot.set_my_short_description(short_description=ABOUT_TEXT),
        ),
        (
            "Description",
            bot.set_my_description(description=DESCRIPTION_TEXT),
        ),
        (
            "Commands",
            bot.set_my_commands(commands=BOT_COMMANDS),
        ),
    )

    for label, request in updates:
        try:
            await request
            LOGGER.info("Telegram bot %s updated successfully.", label)
        except Exception:
            # A profile update failure should not prevent the bot from starting.
            LOGGER.exception("Could not update Telegram bot %s.", label)
