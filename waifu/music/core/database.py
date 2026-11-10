"""Music-specific MongoDB collections shared by feature modules."""
from waifu import db

music_favorites = db["music_favorites"]
music_users = db["music_users"]
music_stats = db["music_stats"]
