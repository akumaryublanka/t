"""YouTube/SoundCloud extraction with cookie-free YouTube clients."""

import logging

from yt_dlp import YoutubeDL

LOGGER = logging.getLogger("waifu.music")


def _best_thumbnail(info: dict | None) -> str | None:
    """Pick the largest usable thumbnail URL from yt-dlp/YouTube Music metadata."""
    if not isinstance(info, dict):
        return None
    direct = info.get("thumbnail")
    if isinstance(direct, str) and direct.startswith(("http://", "https://")):
        return direct
    candidates = []
    raw = info.get("thumbnails") or []
    if isinstance(raw, dict):
        raw = raw.get("thumbnails") or []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        candidates.append((width * height, url))
    return max(candidates, default=(0, None))[1]


def _artist_name(info: dict | None) -> str | None:
    if not isinstance(info, dict):
        return None
    artist = info.get("artist") or info.get("creator") or info.get("uploader")
    if isinstance(artist, (list, tuple)):
        artist = ", ".join(str(value) for value in artist if value)
    return str(artist).strip() if artist else None


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
            "artist": _artist_name(info),
            "uploader": info.get("uploader"),
            "thumbnail": _best_thumbnail(info),
        }



def _ytmusic_search(query: str) -> dict | None:
    """Find a YouTube Music result only after confirming it has playable audio."""
    try:
        from ytmusicapi import YTMusic

        client = YTMusic(language="en", location="IN")
        results = client.search(query, filter="songs", limit=10)
        try:
            signature_timestamp = client.get_signatureTimestamp()
        except Exception as exc:
            LOGGER.info("YouTube Music signature timestamp unavailable: %s", exc)
            signature_timestamp = None

        for item in results:
            video_id = item.get("videoId")
            if not video_id or item.get("isAvailable") is False:
                continue
            try:
                song = client.get_song(
                    video_id,
                    signatureTimestamp=signature_timestamp,
                )
                status = (song.get("playabilityStatus") or {}).get("status")
                if status not in (None, "OK"):
                    continue
                formats = (song.get("streamingData") or {}).get("adaptiveFormats") or []
                playable_audio = any(
                    entry.get("url")
                    and entry.get("mimeType", "").startswith("audio/")
                    for entry in formats
                )
                if not playable_audio:
                    continue
            except Exception as exc:
                LOGGER.debug("Skipping unplayable YouTube Music result %s: %s", video_id, exc)
                continue

            artists = item.get("artists") or []
            artist = ", ".join(
                artist_item.get("name", "")
                for artist_item in artists
                if artist_item.get("name")
            )
            thumbnails = item.get("thumbnails") or []
            thumbnail = _best_thumbnail({"thumbnails": thumbnails})
            return {
                "source": f"https://music.youtube.com/watch?v={video_id}",
                "title": item.get("title") or query,
                "duration": item.get("duration_seconds"),
                "artist": artist or None,
                "uploader": artist or None,
                "thumbnail": thumbnail,
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





def _youtube_video_id(source: str) -> str | None:
    """Extract a YouTube video ID from a watch, music, or short URL."""
    from urllib.parse import parse_qs, urlparse

    try:
        parsed = urlparse(source)
        host = parsed.netloc.lower().split(":", 1)[0]
        if host in {"youtu.be", "www.youtu.be"}:
            return parsed.path.strip("/").split("/", 1)[0] or None
        if host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
            query = parse_qs(parsed.query)
            if query.get("v"):
                return query["v"][0]
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
                return parts[1]
    except Exception:
        return None
    return None


def _download_ytmusic_audio(source: str, temp_dir: str, max_bytes: int) -> dict | None:
    """Resolve and download a YouTube Music audio format using ytmusicapi.

    YTMusic.get_song() returns streamingData.adaptiveFormats; this consumes
    the audio-only URL supplied by that API instead of asking yt-dlp to
    authenticate against YouTube. The returned file is still decoded by
    FFmpeg/PyTgCalls in the existing voice-chat runtime.
    """
    import os
    import re
    import requests
    from ytmusicapi import YTMusic

    ytm = YTMusic(language="en", location="IN")
    video_id = _youtube_video_id(source)
    selected = None

    # YouTube's signed audio URLs can be invalid when ytmusicapi uses its
    # fallback signature timestamp. Fetch the current value before get_song.
    # If YouTube changes the base.js endpoint temporarily, retain the library
    # fallback rather than disabling playback altogether.
    try:
        signature_timestamp = ytm.get_signatureTimestamp()
    except Exception as exc:
        LOGGER.warning("Could not refresh YouTube signature timestamp: %s", exc)
        signature_timestamp = None

    def get_song_with_fresh_signature(candidate_id: str) -> dict:
        return ytm.get_song(candidate_id, signatureTimestamp=signature_timestamp)

    if video_id:
        metadata = get_song_with_fresh_signature(video_id)
    else:
        results = ytm.search(source, filter="songs", limit=5)
        metadata = None
        for result in results:
            candidate_id = result.get("videoId")
            if candidate_id and result.get("isAvailable") is not False:
                try:
                    candidate = get_song_with_fresh_signature(candidate_id)
                    formats = (candidate.get("streamingData") or {}).get("adaptiveFormats") or []
                    if any(item.get("url") and item.get("mimeType", "").startswith("audio/") for item in formats):
                        metadata = candidate
                        video_id = candidate_id
                        break
                except Exception as exc:
                    LOGGER.info("YouTube Music candidate unavailable: %s", exc)
        if metadata is None:
            return None

    status = (metadata.get("playabilityStatus") or {}).get("status")
    if status not in (None, "OK"):
        raise RuntimeError("YouTube Music says this track is not playable.")

    streaming = metadata.get("streamingData") or {}
    formats = [
        item for item in streaming.get("adaptiveFormats", [])
        if item.get("url") and item.get("mimeType", "").startswith("audio/")
    ]
    if not formats:
        return None

    # Prefer a high-bitrate audio-only representation; URLs are short-lived,
    # so retrieve the bytes immediately after get_song().
    selected = max(
        formats,
        key=lambda item: int(item.get("averageBitrate") or item.get("bitrate") or 0),
    )
    stream_url = selected["url"]
    extension = ".m4a" if "audio/mp4" in selected.get("mimeType", "") else ".webm"
    audio_path = os.path.join(temp_dir, f"{video_id}{extension}")

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    written = 0
    with requests.get(stream_url, headers=headers, stream=True, timeout=(15, 45)) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            raise RuntimeError("YouTube Music returned a web page instead of audio.")
        declared_size = int(response.headers.get("Content-Length") or 0)
        if declared_size > max_bytes:
            raise ValueError("The audio file is too large for safe voice-chat playback.")
        with open(audio_path, "wb") as output:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("The audio file is too large for safe voice-chat playback.")
                output.write(chunk)

    if written <= 0:
        raise RuntimeError("YouTube Music returned an empty audio stream.")

    details = metadata.get("videoDetails") or {}
    title = details.get("title") or source
    duration_text = details.get("lengthSeconds")
    try:
        duration = int(duration_text) if duration_text else None
    except (TypeError, ValueError):
        duration = None

    return {
        "path": audio_path,
        "title": title,
        "duration": duration,
        "uploader": details.get("author"),
        "source": f"https://music.youtube.com/watch?v={video_id}",
        "mime_type": selected.get("mimeType"),
    }

def download_track_sync(source: str, max_bytes: int = 80 * 1024 * 1024) -> dict:
    """Download audio for VC playback: YouTube Music API first, then yt-dlp."""
    import glob
    import os
    import shutil
    import tempfile

    if not source or len(source) > 2000:
        raise ValueError("A valid song source is required.")

    # The ytmusicapi path returns an audio-only adaptive format URL via
    # get_song(). This is the primary YouTube path; no browser cookies needed.
    ytm_temp_dir = tempfile.mkdtemp(prefix="mikasa-ytmusic-")
    try:
        ytmusic_audio = _download_ytmusic_audio(source, ytm_temp_dir, max_bytes)
        if ytmusic_audio:
            ytmusic_audio["temp_dir"] = ytm_temp_dir
            LOGGER.info("Prepared voice-chat audio through YouTube Music API: %s",
                        ytmusic_audio.get("title", "Unknown track"))
            return ytmusic_audio
        shutil.rmtree(ytm_temp_dir, ignore_errors=True)
    except Exception as exc:
        shutil.rmtree(ytm_temp_dir, ignore_errors=True)
        LOGGER.warning("YouTube Music API playback path unavailable: %s", exc)

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
    raise RuntimeError(
        "Could not prepare this track for voice-chat playback. " + detail[:280]
    )

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
                "artist": _artist_name(entry),
                "uploader": entry.get("uploader"),
                "thumbnail": _best_thumbnail(entry),
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
