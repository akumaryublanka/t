"""Set the public Telegram profile text and command menu at startup.

The Description Picture is not exposed by the standard Telegram Bot API.
Upload the generated MikasaVerse banner once via @BotFather.
"""
from telegram import BotCommand

from waifu import LOGGER


ABOUT_TEXT = (
    "🎴 Catch waifus & husbandos, collect rare characters, trade, duel and enjoy VC music!"
)

DESCRIPTION_TEXT = (
    "🌸 Welcome to MikasaVerse!\n\n"
    "🎴 Catch anime characters as they drop in your group. Build your collection, "
    "hunt rare editions, earn Neko Coins & Gems, trade or gift characters, and "
    "challenge friends in PvP duels.\n\n"
    "🎧 Enjoy music with friends in voice chats and play mini-games like Quiz, "
    "Mines, Tower and Scramble.\n\n"
    "⚔️ Collect. Trade. Compete. Become a legend!"
)

# Keep this list aligned with the player-facing handlers in waifu/modules/.
# Owner-only maintenance/developer commands are intentionally not advertised.
BOT_COMMANDS = [
    BotCommand("start", "Start MikasaVerse and open the welcome menu"),
    BotCommand("guess", "Catch the active anime character"),
    BotCommand("harem", "View your character collection"),
    BotCommand("profile", "View your player profile and stats"),
    BotCommand("fav", "Set your favorite character"),
    BotCommand("rarity", "View collection rarity statistics"),
    BotCommand("top", "View the global collection leaderboard"),
    BotCommand("ctop", "View the character collection leaderboard"),
    BotCommand("daily", "Claim your daily Neko Coins"),
    BotCommand("balance", "Check your Neko Coins and Gems"),
    BotCommand("market", "Browse character marketplace listings"),
    BotCommand("buy", "Buy a listed character"),
    BotCommand("sell", "List one of your characters for sale"),
    BotCommand("delist", "Remove your character from the market"),
    BotCommand("redeem", "Redeem a reward code"),
    BotCommand("duel", "Challenge another player to a duel"),
    BotCommand("trade", "Trade characters with another player"),
    BotCommand("gift", "Gift a character to another player"),
    BotCommand("marry", "Propose in-game marriage"),
    BotCommand("spouse", "View your in-game spouse"),
    BotCommand("divorce", "End your in-game marriage"),
    BotCommand("couple", "See today's group couple"),
    BotCommand("quiz", "Play an anime image quiz"),
    BotCommand("qlb", "View the quiz leaderboard"),
    BotCommand("mines", "Play the Mines mini-game"),
    BotCommand("cashout", "Cash out your Mines reward"),
    BotCommand("nguess", "Start a character name-guessing round"),
    BotCommand("nguess_stop", "Stop the active name-guessing round"),
    BotCommand("tower", "Play the Infinite Tower challenge"),
    BotCommand("tower_stop", "Stop your Infinite Tower run"),
    BotCommand("scramble", "Guess a scrambled anime character name"),
    BotCommand("scramble_stop", "Stop the active scramble game"),
    BotCommand("hclaim", "Claim a free character when available"),
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
