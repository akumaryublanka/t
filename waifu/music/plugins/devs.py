"""Developer access checks for the music subsystem."""
from waifu import OWNER_ID, sudo_users


def is_music_developer(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in sudo_users
