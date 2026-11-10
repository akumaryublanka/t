"""Per-user music activity storage helpers."""
from datetime import datetime, timezone
from waifu.music.core.database import music_users, music_stats


async def record_play(user_id: int, name: str, chat_id: int, title: str) -> None:
    now = datetime.now(timezone.utc)
    await music_users.update_one(
        {"user_id": user_id},
        {"$set": {"name": name, "last_played_at": now}, "$inc": {"songs_played": 1}},
        upsert=True,
    )
    await music_stats.update_one(
        {"_id": "global"},
        {"$inc": {"songs_played": 1}, "$set": {"last_chat_id": chat_id, "last_title": title, "updated_at": now}},
        upsert=True,
    )


async def get_user(user_id: int) -> dict | None:
    return await music_users.find_one({"user_id": user_id}, {"_id": 0})
