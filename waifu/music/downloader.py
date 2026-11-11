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
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
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
        "socket_timeout": 12,
        "retries": 1,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 3,
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
    """Resolve a quick catalogue result; audio availability is checked at download time."""
    try:
        import requests
        from functools import partial
        from ytmusicapi import YTMusic

        session = requests.Session()
        session.request = partial(session.request, timeout=(4, 10))
        client = YTMusic(
            language="en",
            location="IN",
            requests_session=session,
        )
        results = client.search(query, filter="songs", limit=8)
        candidates = [
            item for item in results
            if item.get("videoId") and item.get("isAvailable") is not False
        ]
        if not candidates:
            return None

        first = candidates[0]
        artists = first.get("artists") or []
        artist = ", ".join(
            artist_item.get("name", "")
            for artist_item in artists
            if artist_item.get("name")
        )
        return {
            "source": f"https://music.youtube.com/watch?v={first['videoId']}",
            "title": first.get("title") or query,
            "duration": first.get("duration_seconds"),
            "artist": artist or None,
            "uploader": artist or None,
            "thumbnail": _best_thumbnail(first),
            "ytmusic_candidates": [item["videoId"] for item in candidates[:4]],
            "search_query": query,
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

        # If anonymous yt-dlp cannot resolve a YouTube URL, pass its video ID
        # to ytmusicapi so the audio-only endpoint can still be tried.
        video_id = _youtube_video_id(query)
        if video_id:
            return {
                "source": query,
                "title": "YouTube track",
                "duration": None,
                "ytmusic_candidates": [video_id],
            }
        raise ValueError(
            "This link could not be played. Try a song title or a SoundCloud link instead."
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


def _download_ytmusic_audio(
    source: str,
    temp_dir: str,
    max_bytes: int,
    candidate_ids: list[str] | None = None,
) -> dict | None:
    """Download the best available audio format using ytmusicapi."""
    import os
    import requests
    from functools import partial
    from ytmusicapi import YTMusic

    session = requests.Session()
    session.request = partial(session.request, timeout=(4, 12))
    ytm = YTMusic(language="en", location="IN", requests_session=session)

    direct_id = _youtube_video_id(source)
    candidates = list(candidate_ids or [])
    if direct_id and direct_id not in candidates:
        candidates.insert(0, direct_id)
    if not candidates and not direct_id:
        results = ytm.search(source, filter="songs", limit=5)
        candidates = [
            item["videoId"] for item in results
            if item.get("videoId") and item.get("isAvailable") is not False
        ]

    if not candidates:
        return None

    try:
        signature_timestamp = ytm.get_signatureTimestamp()
    except Exception as exc:
        LOGGER.info("YouTube Music signature timestamp unavailable: %s", exc)
        signature_timestamp = None

    metadata = None
    selected = None
    video_id = None
    for candidate_id in candidates[:4]:
        try:
            candidate = ytm.get_song(
                candidate_id,
                signatureTimestamp=signature_timestamp,
            )
            status = (candidate.get("playabilityStatus") or {}).get("status")
            if status not in (None, "OK"):
                LOGGER.debug("YouTube Music candidate %s is not playable.", candidate_id)
                continue
            formats = [
                item for item in (candidate.get("streamingData") or {}).get("adaptiveFormats", [])
                if item.get("url") and item.get("mimeType", "").startswith("audio/")
            ]
            if not formats:
                continue
            selected = max(
                formats,
                key=lambda item: int(item.get("averageBitrate") or item.get("bitrate") or 0),
            )
            metadata = candidate
            video_id = candidate_id
            break
        except Exception as exc:
            LOGGER.info("YouTube Music candidate %s unavailable: %s", candidate_id, exc)

    if metadata is None or selected is None or video_id is None:
        return None

    extension = ".m4a" if "audio/mp4" in selected.get("mimeType", "") else ".webm"
    audio_path = os.path.join(temp_dir, f"{video_id}{extension}")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    written = 0
    with requests.get(
        selected["url"],
        headers=headers,
        stream=True,
        timeout=(8, 25),
    ) as response:
        response.raise_for_status()
        if "text/html" in response.headers.get("Content-Type", "").lower():
            raise RuntimeError("YouTube Music returned a web page instead of audio.")
        declared_size = int(response.headers.get("Content-Length") or 0)
        if declared_size > max_bytes:
            raise ValueError("The audio file is too large for safe voice-chat playback.")
        with open(audio_path, "wb") as output:
            for chunk in response.iter_content(chunk_size=512 * 1024):
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
    raw_duration = details.get("lengthSeconds")
    try:
        track_duration = int(raw_duration) if raw_duration else None
    except (TypeError, ValueError):
        track_duration = None

    thumb_info = details.get("thumbnail") or {}
    thumb_list = thumb_info.get("thumbnails", []) if isinstance(thumb_info, dict) else thumb_info
    return {
        "path": audio_path,
        "title": title,
        "duration": track_duration,
        "artist": details.get("author"),
        "uploader": details.get("author"),
        "thumbnail": _best_thumbnail({"thumbnails": thumb_list}),
        "source": f"https://music.youtube.com/watch?v={video_id}",
        "mime_type": selected.get("mimeType"),
    }

def download_track_sync(
    source: str,
    max_bytes: int = 80 * 1024 * 1024,
    ytmusic_candidates: list[str] | None = None,
    fallback_query: str | None = None,
) -> dict:
    """Download audio for VC playback: YouTube Music API first, then yt-dlp."""
    import glob
    import os
    import shutil
    import tempfile

    if not source or len(source) > 2000:
        raise ValueError("A valid song source is required.")

    is_url = source.startswith(("http://", "https://"))
    is_youtube_url = bool(_youtube_video_id(source))
    # YT Music is useful for searches and YouTube links, but avoid searching a
    # SoundCloud/Vimeo URL as though it were a song title.
    if not is_url or is_youtube_url or ytmusic_candidates:
        ytm_temp_dir = tempfile.mkdtemp(prefix="mikasa-ytmusic-")
        try:
            ytmusic_audio = _download_ytmusic_audio(
                source,
                ytm_temp_dir,
                max_bytes,
                ytmusic_candidates,
            )
            if ytmusic_audio:
                ytmusic_audio["temp_dir"] = ytm_temp_dir
                LOGGER.info(
                    "Prepared voice-chat audio through YouTube Music API: %s",
                    ytmusic_audio.get("title", "Unknown track"),
                )
                return ytmusic_audio
            shutil.rmtree(ytm_temp_dir, ignore_errors=True)
        except Exception as exc:
            shutil.rmtree(ytm_temp_dir, ignore_errors=True)
            LOGGER.warning("YouTube Music API playback path unavailable: %s", exc)

    if fallback_query:
        searches = [f"ytsearch1:{fallback_query}", f"scsearch1:{fallback_query}"]
    else:
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
                "artist": _artist_name(info or {}),
                "uploader": (info or {}).get("uploader"),
                "thumbnail": _best_thumbnail(info or {}),
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


def _ytmusic_video_search(query: str, limit: int = 5) -> list[dict]:
    """Resolve video IDs through YouTube Music, avoiding yt-dlp's search API."""
    try:
        import requests
        from functools import partial
        from ytmusicapi import YTMusic

        session = requests.Session()
        session.request = partial(session.request, timeout=(5, 12))
        client = YTMusic(language="en", location="IN", requests_session=session)
        results = []
        for filter_name in ("videos", "songs"):
            try:
                found = client.search(query, filter=filter_name, limit=limit)
            except Exception as exc:
                LOGGER.info("YouTube Music %s search unavailable: %s", filter_name, exc)
                continue
            for item in found or []:
                video_id = item.get("videoId")
                if video_id and item.get("isAvailable") is not False:
                    results.append({
                        "id": video_id,
                        "title": item.get("title") or query,
                        "artist": ", ".join(
                            artist.get("name", "")
                            for artist in (item.get("artists") or [])
                            if artist.get("name")
                        ) or None,
                        "thumbnail": _best_thumbnail(item),
                        "duration": item.get("duration_seconds"),
                    })
            if results:
                break
        unique = []
        seen = set()
        for item in results:
            if item["id"] not in seen:
                seen.add(item["id"])
                unique.append(item)
        return unique[:limit]
    except Exception as exc:
        LOGGER.warning("YouTube Music video search failed: %s", exc)
        return []


def _youtube_video_options(player_client: str | None = None) -> dict:
    """Build video options with progressive and DASH format fallbacks."""
    import shutil

    options = {
        **_youtube_options(),
        # Prefer a compact progressive file; fall back to separate DASH video/audio.
        "format": (
            "best[height<=480]/"
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=720]/"
            "bestvideo+bestaudio/best"
        ),
        "skip_download": False,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 1,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 2,
    }
    if player_client:
        options["extractor_args"] = {
            "youtube": {"player_client": [player_client]}
        }

    # Render's base Python image may not provide a system ffmpeg executable.
    # imageio-ffmpeg bundles one so yt-dlp can merge DASH streams when needed.
    try:
        import imageio_ffmpeg
        options["ffmpeg_location"] = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            options["ffmpeg_location"] = system_ffmpeg
    return options


def download_video_sync(
    source: str,
    max_bytes: int = 72 * 1024 * 1024,
    max_duration: int = 600,
) -> dict:
    """Download a short video for VC playback using resilient YouTube clients."""
    import glob
    import os
    import shutil
    import tempfile

    if not source or len(source) > 1000:
        raise ValueError("Provide a valid video title or link.")

    is_url = source.startswith(("http://", "https://"))
    if is_url:
        video_id = _youtube_video_id(source)
        candidates = [{
            "id": video_id,
            "url": source,
            "title": source,
            "artist": None,
            "thumbnail": None,
            "duration": None,
        }] if video_id else [{
            "url": source,
            "title": source,
        }]
    else:
        candidates = _ytmusic_video_search(source, limit=3)
        # Keep yt-dlp search as a last resort if the catalogue is unavailable.
        if not candidates:
            candidates = [{"search": f"ytsearch1:{source}", "title": source}]

    errors = []
    for candidate in candidates:
        video_url = candidate.get("url") or (
            f"https://www.youtube.com/watch?v={candidate['id']}"
            if candidate.get("id") else None
        )
        search = video_url or candidate.get("search")
        if not search:
            continue

        # Android and web clients increasingly return formats that need PO
        # tokens. Try token-free clients separately, then continue to the next
        # catalogue result if this particular video is unavailable.
        clients = ("tv", "android_vr") if candidate.get("id") else (None,)
        for player_client in clients:
            temp_dir = tempfile.mkdtemp(prefix="mikasa-vplay-")
            options = {
                **_youtube_video_options(player_client),
                "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
                "merge_output_format": "mp4",
                "max_filesize": max_bytes,
                "overwrites": True,
            }
            try:
                with YoutubeDL(options) as ydl:
                    info = ydl.extract_info(search, download=True)
                if info and info.get("entries"):
                    info = next((entry for entry in info["entries"] if entry), None)
                if not info:
                    raise RuntimeError("No video result was returned.")

                raw_duration = info.get("duration") or candidate.get("duration")
                video_duration = int(raw_duration) if raw_duration else None
                if video_duration and video_duration > max_duration:
                    raise ValueError(
                        f"Video is too long. The VC video limit is {max_duration // 60} minutes."
                    )

                allowed = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
                files = [
                    path for path in glob.glob(os.path.join(temp_dir, "*"))
                    if os.path.isfile(path)
                    and os.path.splitext(path)[1].lower() in allowed
                    and not path.endswith(".part")
                ]
                if not files:
                    raise RuntimeError("The downloader did not produce a playable video.")
                video_path = max(files, key=os.path.getsize)
                size = os.path.getsize(video_path)
                if size <= 0 or size > max_bytes:
                    raise ValueError("Video is empty or too large for safe VC playback.")

                LOGGER.info(
                    "Prepared VC video with YouTube client %s: %s",
                    player_client or "default",
                    info.get("title") or candidate.get("title") or "Video",
                )
                return {
                    "path": video_path,
                    "temp_dir": temp_dir,
                    "title": info.get("title") or candidate.get("title") or "Video",
                    "duration": video_duration,
                    "uploader": info.get("uploader") or info.get("channel") or candidate.get("artist"),
                    "artist": _artist_name(info) or candidate.get("artist"),
                    "thumbnail": _best_thumbnail(info) or candidate.get("thumbnail"),
                    "source": info.get("webpage_url") or video_url or source,
                    "media_type": "video",
                }
            except Exception as exc:
                errors.append(str(exc))
                shutil.rmtree(temp_dir, ignore_errors=True)
                LOGGER.warning(
                    "VC video download failed for %s via %s: %s",
                    candidate.get("id") or candidate.get("search") or "direct link",
                    player_client or "default",
                    exc,
                )

    detail = errors[-1] if errors else "No playable video was found."
    raise RuntimeError("Could not prepare this video for voice-chat playback. " + detail[:240])



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
