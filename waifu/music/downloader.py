"""YouTube/SoundCloud extraction with optional private YouTube cookies."""

import base64
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
import shutil

from yt_dlp import YoutubeDL

LOGGER = logging.getLogger("waifu.music")


def _cookie_file(temp_dir: str) -> str | None:
    """Copy cookies into writable temporary storage before yt-dlp uses them.

    Render Secret Files are read-only. yt-dlp may update its cookie jar when
    closing, so never pass the mounted secret path directly to yt-dlp.
    """
    configured_path = os.getenv("YTDLP_COOKIES_FILE")
    source_path = Path(configured_path).expanduser() if configured_path else None

    if source_path is None:
        local_cookie_file = Path("cookies.txt")
        if local_cookie_file.is_file():
            source_path = local_cookie_file

    if source_path is not None:
        if not source_path.is_file():
            raise FileNotFoundError(
                "YTDLP_COOKIES_FILE is set, but the configured cookies file does not exist."
            )
        cookie_path = Path(temp_dir) / "cookies.txt"
        shutil.copyfile(source_path, cookie_path)
        try:
            cookie_path.chmod(0o600)
        except OSError:
            LOGGER.warning("Could not restrict permissions on temporary YouTube cookie file.")
        return str(cookie_path)

    encoded = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
    raw = os.getenv("YOUTUBE_COOKIES", "")
    if encoded:
        try:
            content = base64.b64decode(encoded, validate=True).decode("utf-8")
        except Exception as exc:
            raise ValueError("YOUTUBE_COOKIES_B64 must be valid base64-encoded Netscape cookies.") from exc
    else:
        content = raw

    if not content.strip():
        return None
    if "Netscape HTTP Cookie File" not in content and "#HttpOnly_" not in content:
        raise ValueError(
            "YouTube cookies must be exported in Netscape cookies.txt format."
        )

    cookie_path = Path(temp_dir) / "cookies.txt"
    cookie_path.write_text(content, encoding="utf-8")
    try:
        cookie_path.chmod(0o600)
    except OSError:
        LOGGER.warning("Could not restrict permissions on temporary YouTube cookie file.")
    return str(cookie_path)


@contextmanager
def _youtube_dl(options: dict):
    """Create a yt-dlp instance with cookies applied to both search and stream refresh."""
    with tempfile.TemporaryDirectory(prefix="mikasa-yt-dlp-") as temp_dir:
        cookie_file = _cookie_file(temp_dir)
        configured = dict(options)
        if cookie_file:
            configured["cookiefile"] = cookie_file
        with YoutubeDL(configured) as ydl:
            yield ydl


def _extract(search: str) -> dict | None:
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 2,
    }
    with _youtube_dl(options) as ydl:
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
        result = _extract(query)
        if result:
            return result
        raise ValueError(
            "This link could not be played. YouTube may require valid authentication "
            "cookies on the hosting server; try a song title or a SoundCloud link."
        )

    errors = []
    for search in (f"ytsearch1:{query}", f"scsearch1:{query}"):
        try:
            result = _extract(search)
            if result:
                return result
        except Exception as exc:
            errors.append(str(exc))

    if errors:
        raise RuntimeError(
            "YouTube search was blocked and SoundCloud fallback did not return a "
            "playable result. " + " | ".join(errors[-2:])
        )
    raise ValueError("No playable result was found on YouTube or SoundCloud.")


def stream_url_sync(source: str) -> str:
    """Refresh the direct stream URL using the same cookie configuration as search."""
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 20,
        "retries": 2,
    }
    with _youtube_dl(options) as ydl:
        info = ydl.extract_info(source, download=False)
        if info and info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), None)
        url = (info or {}).get("url")
        if not url:
            raise ValueError("Could not refresh the audio stream URL.")
        return url
