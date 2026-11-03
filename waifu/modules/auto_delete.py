"""
modules/auto_delete.py — Centralized Auto Delete Module
=======================================================

All message-deletion timing and scheduling lives in this module.
"""

import asyncio
from typing import Callable

from waifu import LOGGER


async def delete_now(
    bot,
    chat_id: int,
    message_id: int,
    *,
    ignore_errors: bool = False,
) -> None:
    """Delete a message immediately. Set ignore_errors to suppress Telegram API failures."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        if not ignore_errors:
            raise
        return
    LOGGER.debug("Auto-deleted msg %s in chat %s", message_id, chat_id)


async def delete_after(
    bot,
    chat_id: int,
    message_id: int,
    delay: int,
    on_complete: Callable[[], None] | None = None,
) -> None:
    """Delete a message after the specified delay, then run optional cleanup."""
    await asyncio.sleep(max(0, delay))
    try:
        await delete_now(bot, chat_id, message_id, ignore_errors=True)
    finally:
        if on_complete is not None:
            try:
                on_complete()
            except Exception as exc:
                LOGGER.debug("Auto-delete cleanup failed for msg %s: %s", message_id, exc)


def schedule_delete(
    bot,
    chat_id: int,
    message_id: int,
    delay: int,
    on_complete: Callable[[], None] | None = None,
) -> None:
    """Schedule a non-blocking message deletion in one central place."""
    asyncio.create_task(delete_after(bot, chat_id, message_id, delay, on_complete))


# ── Delay constants (seconds) ────────────────────────────────────────────────
WAIFU_DROP_DELAY       = 0    # Delete immediately when claimed or expired
DROP_EXPIRY_DELAY      = 300  # An unclaimed drop expires after 5 minutes
DROP_NOTICE_DELAY      = 5    # Delete the "character got away" notice after 5 seconds
NGUESS_DELAY           = 120  # NGuess messages: 2 minutes
MINES_DELAY            = 300  # Mines game message expires after 5 minutes
HCLAIM_DELAY           = 300  # Hclaim message: 5 minutes
QUIZ_DELAY             = 60   # Quiz messages: 1 minute
WRONG_GUESS_DELAY      = 3    # Temporary incorrect-guess replies: 3 seconds
GUESS_COMMAND_DELAY    = 1    # Remove the user's /guess command after 1 second
