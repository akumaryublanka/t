"""YouTube/search extraction helpers used by the music player."""

from yt_dlp import YoutubeDL


def extract_info_sync(query: str) -> dict:
    search = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(search, download=False)
        if info and info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            raise ValueError("No playable audio result was found.")

        source = info.get("webpage_url") or info.get("original_url") or query
        title = info.get("title") or "Unknown track"
        duration = info.get("duration")
        if not info.get("url"):
            raise ValueError("The source did not provide a playable audio stream.")
        return {
            "source": source,
            "title": title,
            "duration": int(duration) if duration else None,
        }


def stream_url_sync(source: str) -> str:
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(source, download=False)
        if info and info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), None)
        url = (info or {}).get("url")
        if not url:
            raise ValueError("Could not refresh the audio stream URL.")
        return url
