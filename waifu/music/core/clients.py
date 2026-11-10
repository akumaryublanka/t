"""Accessors for the music assistant and its PyTgCalls client."""
from waifu.music import runtime


def user_client():
    return runtime.get_music_client()


def calls_client():
    return runtime.get_calls()
