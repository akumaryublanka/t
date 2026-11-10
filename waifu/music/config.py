"""Environment configuration for the optional music assistant."""

import os


def get_music_credentials():
    """Return (credentials, status, missing_names) without exposing secret values."""
    names = ("MUSIC_API_ID", "MUSIC_API_HASH", "MUSIC_SESSION_STRING")
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        return None, "missing_credentials", missing

    try:
        api_id = int(values["MUSIC_API_ID"])
    except ValueError:
        return None, "invalid_api_id", []

    return (
        (api_id, values["MUSIC_API_HASH"], values["MUSIC_SESSION_STRING"]),
        "ready",
        [],
    )
