"""Load the separately maintained MikasaVerse music package."""

from waifu.music.handlers import register_handlers
from waifu.music.runtime import start_music, stop_music

register_handlers()
