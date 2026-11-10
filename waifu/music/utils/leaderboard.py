"""Music play-count leaderboard queries."""
from waifu.music.core.database import music_users


async def top_users(limit: int = 10) -> list[dict]:
    limit = max(1, min(int(limit), 50))
    cursor = music_users.find({}, {"_id": 0, "user_id": 1, "name": 1, "songs_played": 1}).sort("songs_played", -1).limit(limit)
    return [row async for row in cursor]
