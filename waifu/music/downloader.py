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



def _ytmusic_search(query: str) -> dict | None:
    """Find a song and metadata through the public YouTube Music catalogue."""
    try:
        from ytmusicapi import YTMusic

        client = YTMusic(language="en", location="IN")
        results = client.search(query, filter="songs", limit=5)
        for item in results:
            video_id = item.get("videoId")
            if not video_id or item.get("isAvailable") is False:
                continue
            artists = item.get("artists") or []
            artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            title = item.get("title") or query
            if artist:
                title = f"{title} - {artist}"
            return {
                "source": f"https://www.youtube.com/watch?v={video_id}",
                "title": title,
                "duration": item.get("duration_seconds"),
                "uploader": artist or None,
            }
    except Exception as exc:
        LOGGER.info("YouTube Music catalogue search unavailable: %s", exc)
    return None

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
    # YouTube Music provides a strong song catalogue/search fallback. It returns
    # metadata and video IDs, not playable audio bytes; yt-dlp still prepares audio.
    ytmusic_result = _ytmusic_search(query)
    if ytmusic_result:
        return ytmusic_result

    # Keep yt-dlp and SoundCloud as fallbacks when the catalogue is unavailable.
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




def download_track_sync(source: str, max_bytes: int = 80 * 1024 * 1024) -> dict:
    """Download original audio to a temporary local file for VC playback."""
    import glob
    import os
    import shutil
    import tempfile

    if not source or len(source) > 2000:
        raise ValueError("A valid song source is required.")
    is_url = source.startswith(("http://", "https://"))
    searches = [source] if is_url else [f"ytsearch1:{source}", f"scsearch1:{source}"]
    errors = []
    for search in searches:
        temp_dir = tempfile.mkdtemp(prefix="mikasa-vc-")
        options = {
            **_youtube_options(),
            "format": "bestaudio/best",
            "skip_download": False,
            "noplaylist": True,
            "playlist_items": "1",
            "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
            "max_filesize": max_bytes,
            "overwrites": True,
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(search, download=True)
            if info and info.get("entries"):
                info = next((entry for entry in info["entries"] if entry), None)
            ignored = {".part", ".ytdl", ".json", ".jpg", ".jpeg", ".png", ".webp"}
            files = [
                path for path in glob.glob(os.path.join(temp_dir, "*"))
                if os.path.isfile(path)
                and os.path.splitext(path)[1].lower() not in ignored
                and not path.endswith(".part")
            ]
            if not files:
                raise RuntimeError("yt-dlp did not create a playable audio file.")
            audio_path = max(files, key=os.path.getsize)
            size = os.path.getsize(audio_path)
            if size <= 0:
                raise RuntimeError("The downloaded audio file is empty.")
            if size > max_bytes:
                raise ValueError("The song is too large for safe voice-chat streaming.")
            return {
                "path": audio_path,
                "temp_dir": temp_dir,
                "title": (info or {}).get("title") or "Unknown track",
                "duration": int(info["duration"]) if (info or {}).get("duration") else None,
                "uploader": (info or {}).get("artist") or (info or {}).get("uploader"),
                "source": (info or {}).get("webpage_url") or (info or {}).get("original_url") or source,
            }
        except Exception as exc:
            errors.append(str(exc))
            shutil.rmtree(temp_dir, ignore_errors=True)
            LOGGER.warning("VC audio download failed for %s: %s",
                           search.split(":", 1)[0] if ":" in search else "direct URL", exc)
    detail = errors[-1] if errors else "No playable audio was found."
    raise RuntimeError("Could not prepare this track for voice-chat playback. "
                       "Try another song or a SoundCloud link. " + detail[:280])

def extract_playlist_sync(query: str, max_tracks: int = 20) -> list[dict]:
    """Return a bounded list of flat playlist entries without downloading media."""
    if not 1 <= max_tracks <= 50:
        raise ValueError("Playlist limit must be between 1 and 50.")

    options = {
        **_youtube_options(),
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "playlistend": max_tracks,
        "ignoreerrors": True,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)

    if not info or not info.get("entries"):
        return []

    tracks = []
    seen = set()
    for entry in info["entries"]:
        if not entry:
            continue
        video_id = entry.get("id")
        source = entry.get("webpage_url") or entry.get("url")
        if source and not str(source).startswith(("http://", "https://")):
            source = None
        if not source and video_id:
            source = f"https://www.youtube.com/watch?v={video_id}"
        if not source or source in seen:
            continue
        seen.add(source)
        tracks.append(
            {
                "source": source,
                "title": entry.get("title") or "Unknown track",
                "duration": int(entry["duration"]) if entry.get("duration") else None,
            }
        )
        if len(tracks) >= max_tracks:
            break
    return tracks



def download_audio_sync(query: str, max_bytes: int = 45 * 1024 * 1024) -> dict:
    """Download one audio result to a temporary MP3 for Telegram upload.

    YouTube is tried first for text searches, followed by SoundCloud. This
    deliberately uses the same anonymous clients as playback and never needs
    session cookies. The caller must remove the returned temp_dir.
    """
    import glob
    import os
    import shutil
    import tempfile

    if not query or len(query) > 500:
        raise ValueError("Provide a song title or a valid media link.")

    is_url = query.startswith(("http://", "https://"))
    searches = [query] if is_url else [f"ytsearch1:{query}", f"scsearch1:{query}"]
    errors = []

    for search in searches:
        temp_dir = tempfile.mkdtemp(prefix="mikasa-song-")
        options = {
            **_youtube_options(),
            "skip_download": False,
            "noplaylist": True,
            "playlist_items": "1",
            "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
            "max_filesize": max_bytes,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(search, download=True)
            if info and info.get("entries"):
                info = next((entry for entry in info["entries"] if entry), None)
            files = [
                path for path in glob.glob(os.path.join(temp_dir, "*"))
                if os.path.isfile(path)
            ]
            if not files:
                raise RuntimeError("The downloader did not produce an audio file.")
            audio_path = next(
                (path for path in files if path.lower().endswith(".mp3")),
                max(files, key=os.path.getsize),
            )
            size = os.path.getsize(audio_path)
            if size > max_bytes:
                raise ValueError("The audio file is larger than Telegram's safe upload limit.")
            return {
                "path": audio_path,
                "temp_dir": temp_dir,
                "title": (info or {}).get("title") or "Downloaded audio",
                "duration": int(info["duration"]) if (info or {}).get("duration") else None,
                "uploader": (info or {}).get("artist") or (info or {}).get("uploader"),
            }
        except Exception as exc:
            errors.append(str(exc))
            shutil.rmtree(temp_dir, ignore_errors=True)
            LOGGER.warning("Audio download failed for %s: %s", search.split(":", 1)[0], exc)

    detail = errors[-1] if errors else "No audio result was found."
    raise RuntimeError(
        "Could not download this song without authentication. "
        "Try another title or a SoundCloud link. " + detail[:300]
    )
