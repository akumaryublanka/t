"""Reusable guards for Telegram music command handlers."""
from functools import wraps
from waifu.music import runtime
from waifu.music.formatting import group_only, reply


def music_only(func):
    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        if not group_only(update):
            await reply(update, "🎵 Music commands work inside a group.")
            return None
        if runtime.get_calls() is None:
            await reply(update, runtime.unavailable_message())
            return None
        return await func(update, context, *args, **kwargs)
    return wrapped
