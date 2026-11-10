"""YouTube/SoundCloud extraction with cookie-free YouTube clients."""

import logging

from yt_dlp import YoutubeDL

LOGGER = logging.getLogger("waifu.music")


def _youtube_options() -> dict:
    """Prefer YouTube clients that can work without a logged-in session."""
    return {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
    }


def _extract(search: str) -> dict | None:
    with YoutubeDL(_youtube_options()) as ydl:
        info = ydl.extract_info(search, download=False)
        if info and info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            return None
        source = info.get("webpage_url") or info.get("original_url")
        if not source or not info.get("url"):
            return None
        return {
            "source": source,
            "title": info.get("title") or "Unknown track",
            "duration": int(info["duration"]) if info.get("duration") else None,
        }


def extract_info_sync(query: str) -> dict:
    is_url = query.startswith(("http://", "https://"))
    if is_url:
        try:
            result = _extract(query)
            if result:
                return result
        except Exception as exc:
            LOGGER.warning("Direct music link extraction failed: %s", exc)
        raise ValueError(
            "This link could not be played without authentication. Try a song title "
            "or a SoundCloud link instead."
        )

    errors = []
    # Try YouTube first, then SoundCloud if YouTube blocks anonymous extraction.
    for search in (f"ytsearch1:{query}", f"scsearch1:{query}"):
        try:
            result = _extract(search)
            if result:
                return result
        except Exception as exc:
            errors.append(str(exc))
            LOGGER.warning("Music search failed for %s: %s", search.split(":", 1)[0], exc)

    if errors:
        raise RuntimeError(
            "Anonymous YouTube extraction was blocked and SoundCloud fallback "
            "did not return a playable result. Try a SoundCloud link or another track."
        )
    raise ValueError("No playable result was found on YouTube or SoundCloud.")


def stream_url_sync(source: str) -> str:
    """Refresh the direct stream URL without requiring YouTube cookies."""
    with YoutubeDL(_youtube_options()) as ydl:
        info = ydl.extract_info(source, download=False)
        if info and info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), None)
        url = (info or {}).get("url")
        if not url:
            raise ValueError("Could not refresh the audio stream URL.")
        return url
