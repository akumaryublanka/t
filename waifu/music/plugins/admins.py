"""Group administrator lookup for optional moderation controls."""
from waifu import application


async def is_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await application.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False
