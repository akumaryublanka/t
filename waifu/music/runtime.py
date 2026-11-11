"""Reliable voice-chat playback lifecycle for MikasaVerse."""

import asyncio
import logging
import os
import shutil
from collections import defaultdict
from html import escape

from pyrogram import Client as PyrogramClient
from pytgcalls import PyTgCalls
from pytgcalls import filters as call_filters
from pytgcalls.types import AudioQuality, GroupCallConfig, MediaStream, StreamEnded, VideoQuality

from waifu import application
from waifu.music.config import get_music_credentials
from waifu.music.downloader import download_track_sync, download_video_sync, get_related_tracks_sync
from waifu.music.formatting import duration, player_keyboard
from waifu.music.thumbnail import create_music_thumbnail

LOGGER = logging.getLogger("waifu.music")

_music_client: PyrogramClient | None = None
_calls: PyTgCalls | None = None
_sessions: dict[int, dict] = {}
_chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_resolved_chat_peers: set[int] = set()
_startup_status = "not_started"

# PyTgCalls identifies a stream-end update by chat, not by our track ID.
# A short guard prevents a delayed end event from the replaced stream from
# skipping the track that has just started.
STREAM_END_GUARD_SECONDS = 1.35


def _new_state() -> dict:
    return {
        "current": None,
        "queue": [],
        "volume": 100,
        "loop_remaining": 0,
        "paused": False,
        "active_temp_dir": None,
        "active_path": None,
        "active_kind": "audio",
        "generation": 0,
        "started_at": 0.0,
        "transitioning": False,
        "recovering": False,
        "autoplay": False,
        "autoplay_seen": [],
    }


def _get_state(chat_id: int) -> dict:
    state = _sessions.setdefault(chat_id, _new_state())
    # Migrate old in-memory states created before the lifecycle update.
    for key, value in _new_state().items():
        state.setdefault(key, value)
    return state


def get_calls() -> PyTgCalls | None:
    return _calls


def get_music_client() -> PyrogramClient | None:
    return _music_client


def get_session(chat_id: int) -> dict | None:
    """Return the live session for internal handlers; callers must use its lock."""
    return _sessions.get(chat_id)


async def session_snapshot(chat_id: int) -> dict | None:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state:
            return None
        return {
            **state,
            "current": dict(state["current"]) if state.get("current") else None,
            "queue": [dict(track) for track in state.get("queue", [])],
        }


async def _cleanup_temp_dir(path: str | None) -> None:
    if path:
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)


async def _leave_call(chat_id: int) -> None:
    try:
        if _calls is not None:
            await _calls.leave_call(chat_id)
    except Exception:
        LOGGER.debug("Voice chat was already left in %s.", chat_id, exc_info=True)


async def _stop_chat_locked(chat_id: int, *, leave_call: bool = True) -> None:
    """Stop and clear a chat. Caller must already hold _chat_locks[chat_id]."""
    state = _sessions.pop(chat_id, None)
    if state:
        state["generation"] = int(state.get("generation", 0)) + 1
        state["transitioning"] = False
        state["recovering"] = False
    if leave_call:
        await _leave_call(chat_id)
    if state:
        await _cleanup_temp_dir(state.get("active_temp_dir"))


async def stop_chat(chat_id: int) -> None:
    """Stop one group's stream and release its downloaded audio."""
    async with _chat_locks[chat_id]:
        await _stop_chat_locked(chat_id)


def unavailable_message() -> str:
    if _startup_status == "missing_credentials":
        return (
            "⚙️ <b>Music assistant is not configured yet.</b>\n"
            "The owner must add <code>MUSIC_API_ID</code>, "
            "<code>MUSIC_API_HASH</code> and <code>MUSIC_SESSION_STRING</code> "
            "to Render Environment."
        )
    if _startup_status == "invalid_api_id":
        return "⚠️ <b>Music setup has an invalid MUSIC_API_ID.</b> The owner should check Render Environment."
    if _startup_status == "missing_ffmpeg":
        return "⚠️ <b>Music playback is unavailable.</b> The Render service needs ffmpeg and ffprobe."
    if _startup_status == "startup_failed":
        return (
            "⚠️ <b>Music assistant failed to start.</b> "
            "The owner should check the Render logs; the credentials were detected."
        )
    return "⚠️ <b>Music assistant is starting or temporarily unavailable.</b> Please try again shortly."


async def _send_chat_message(chat_id: int, text: str, reply_markup=None) -> None:
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception:
        LOGGER.exception("Could not send music status message to %s", chat_id)


async def _send_now_playing(chat_id: int, track: dict) -> None:
    """Send the anime-style player card without delaying the queue transition."""
    heading = "🎬 <b>VIDEO STARTED STREAMING</b>" if track.get("media_type") == "video" else "➜ <b>STARTED STREAMING</b>"
    caption = (
        f"{heading} |\n\n"
        f"▸ <b>TITLE:</b> {escape(str(track.get('title') or 'Unknown Track'))}\n"
        f"▸ <b>DURATION:</b> {duration(track.get('duration'))} MINUTES\n"
        f"▸ <b>REQUESTED BY:</b> {escape(str(track.get('requested_by') or 'Unknown'))}"
    )

    async def send_card() -> None:
        image = await asyncio.to_thread(
            create_music_thumbnail,
            title=track.get("title", "Unknown track"),
            artist=artist,
            artwork=track.get("thumbnail"),
            duration=track.get("duration"),
            requester=track.get("requested_by"),
        )
        await application.bot.send_photo(
            chat_id=chat_id,
            photo=image,
            caption=caption,
            parse_mode="HTML",
            reply_markup=player_keyboard(chat_id),
        )

    try:
        await asyncio.wait_for(send_card(), timeout=4.0)
    except Exception:
        LOGGER.info("Player artwork timed out or failed; using text player.", exc_info=True)
        await _send_chat_message(
            chat_id,
            caption,
            reply_markup=player_keyboard(chat_id),
        )


async def _find_chat_in_dialogs(chat_id: int) -> bool:
    """Last-resort peer lookup; avoid scanning every dialog on normal playback."""
    if _music_client is None:
        return False
    async for dialog in _music_client.get_dialogs():
        if dialog.chat and dialog.chat.id == chat_id:
            _resolved_chat_peers.add(chat_id)
            LOGGER.info("Music assistant resolved group peer %s from its dialogs.", chat_id)
            return True
    return False


async def _ensure_chat_peer(chat_id: int) -> None:
    """Resolve a known peer directly, otherwise join with a one-use invite."""
    if _music_client is None:
        raise RuntimeError("The music assistant Telegram session is not running.")
    if chat_id in _resolved_chat_peers:
        return

    # get_chat is a direct lookup and avoids the slow full-dialog scan when the
    # assistant has already seen this group in its local peer database.
    try:
        await _music_client.get_chat(chat_id)
        _resolved_chat_peers.add(chat_id)
        return
    except Exception:
        pass

    invite_link = None
    try:
        invite = await application.bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            name="MikasaVerse music assistant",
        )
        invite_link = invite.invite_link
        joined_chat = await _music_client.join_chat(invite_link)
        joined_chat_id = getattr(joined_chat, "id", None)
        if joined_chat_id is not None and joined_chat_id != chat_id:
            try:
                await _music_client.leave_chat(joined_chat_id)
            except Exception:
                LOGGER.warning("Could not leave an unexpected chat joined via invite link.")
            raise RuntimeError("The generated invite link did not lead to the command group.")

        if joined_chat_id == chat_id:
            _resolved_chat_peers.add(chat_id)
            LOGGER.info("Music assistant automatically joined group %s.", chat_id)
            return
        # Rare case: the assistant was already a participant but its peer wasn't cached.
        if await _music_client.get_chat(chat_id):
            _resolved_chat_peers.add(chat_id)
            return
        raise RuntimeError("Telegram did not confirm that the assistant joined this group.")
    except Exception as exc:
        try:
            if await _music_client.get_chat(chat_id):
                _resolved_chat_peers.add(chat_id)
                return
        except Exception:
            try:
                if await _find_chat_in_dialogs(chat_id):
                    return
            except Exception:
                LOGGER.debug("Could not refresh assistant dialogs after auto-join failure.", exc_info=True)
        raise RuntimeError(
            "Automatic group join failed. Make sure the main MikasaVerse bot is an admin "
            "with Invite Users permission, and that the group allows the assistant to join."
        ) from exc
    finally:
        if invite_link:
            try:
                await application.bot.revoke_chat_invite_link(
                    chat_id=chat_id,
                    invite_link=invite_link,
                )
            except Exception:
                LOGGER.warning("Could not revoke the temporary invite link in %s.", chat_id)


def _audio_stream(path: str) -> MediaStream:
    """Create a stable 48 kHz stereo audio stream with timestamp correction."""
    return MediaStream(
        path,
        AudioQuality.HIGH,
        audio_flags=MediaStream.Flags.REQUIRED,
        video_flags=MediaStream.Flags.IGNORE,
        ffmpeg_parameters=(
            "-re -fflags +genpts "
            "--audio -atmid -af aresample=48000:async=1:first_pts=0"
        ),
    )


def _video_stream(path: str) -> MediaStream:
    """Create a 720p video+audio stream, paced in real time."""
    return MediaStream(
        path,
        AudioQuality.HIGH,
        VideoQuality.HD_720p,
        audio_flags=MediaStream.Flags.REQUIRED,
        video_flags=MediaStream.Flags.REQUIRED,
        ffmpeg_parameters=(
            "-re -fflags +genpts "
            "--audio -atmid -af aresample=48000:async=1:first_pts=0"
        ),
    )


def _media_stream(path: str, kind: str) -> MediaStream:
    return _video_stream(path) if kind == "video" else _audio_stream(path)


async def _run_blocking_download(func, *, timeout: float, **kwargs) -> dict:
    """Run a downloader with a deadline and clean up its result if it finishes late."""
    task = asyncio.create_task(asyncio.to_thread(func, **kwargs))

    def cleanup_late_result(done: asyncio.Task) -> None:
        try:
            result = done.result()
        except BaseException:
            return
        if isinstance(result, dict) and result.get("temp_dir"):
            asyncio.create_task(_cleanup_temp_dir(result["temp_dir"]))

    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.add_done_callback(cleanup_late_result)
        raise


async def _download_track(track: dict, chat_id: int) -> dict:
    """Download media with bounded waits so requests cannot hang a group forever."""
    kind = track.get("media_type", "audio")
    if kind == "video":
        try:
            return await _run_blocking_download(
                download_video_sync,
                timeout=75,
                source=track["source"],
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Video preparation timed out. Try a shorter video or another link.") from exc

    try:
        return await _run_blocking_download(
            download_track_sync,
            timeout=55,
            source=track["source"],
            ytmusic_candidates=track.get("ytmusic_candidates"),
            fallback_query=track.get("search_query"),
            expected_duration=track.get("expected_duration"),
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Audio preparation timed out. Please try the song again.") from exc
    except Exception as source_error:
        title = (track.get("title") or "").strip()
        if "preview" in str(source_error).casefold():
            LOGGER.warning("Only a short preview was available for '%s'.", title)
            raise RuntimeError(
                "Only a short preview was available; the full song could not be verified, so it was not played."
            ) from source_error
        if not title or title == track.get("source") or track.get("search_query"):
            LOGGER.warning("Primary music source failed in chat %s: %s", chat_id, source_error)
            raise RuntimeError("This track is temporarily unavailable.") from source_error
        LOGGER.info("Primary source unavailable for '%s'; trying title search.", title)
        try:
            return await _run_blocking_download(
                download_track_sync,
                timeout=45,
                source=title,
            )
        except Exception as fallback_error:
            LOGGER.warning("No alternate audio source for '%s': %s", title, fallback_error)
            raise RuntimeError("This track is temporarily unavailable.") from fallback_error


async def _start_track(chat_id: int, track: dict) -> None:
    """Prepare media outside the chat lock; lock only the final state/stream swap."""
    if _calls is None:
        raise RuntimeError("Music assistant is not connected.")

    state = _get_state(chat_id)
    state["transitioning"] = True
    downloaded = None
    try:
        await _ensure_chat_peer(chat_id)
        downloaded = await _download_track(track, chat_id)
        path = downloaded.get("path")
        temp_dir = downloaded.get("temp_dir")
        if not path or not temp_dir or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise RuntimeError("The audio downloader returned an empty or missing file.")

        if downloaded.get("source"):
            track["source"] = downloaded["source"]
        if downloaded.get("title"):
            track["title"] = downloaded["title"]
        if downloaded.get("duration") is not None:
            track["duration"] = downloaded["duration"]
        for key in ("artist", "uploader", "thumbnail"):
            if downloaded.get(key):
                track[key] = downloaded[key]

        media_kind = downloaded.get("media_type") or track.get("media_type", "audio")
        async with _chat_locks[chat_id]:
            # A /end command may have removed this session while the download ran.
            if _sessions.get(chat_id) is not state or not state.get("transitioning"):
                raise RuntimeError("This playback request was cancelled.")
            await _calls.play(
                chat_id,
                _media_stream(path, media_kind),
                GroupCallConfig(auto_start=False),
            )

            old_temp_dir = state.get("active_temp_dir")
            state["current"] = track
            state["active_temp_dir"] = temp_dir
            state["active_path"] = path
            state["active_kind"] = media_kind
            state["paused"] = False
            state["generation"] = int(state.get("generation", 0)) + 1
            state["started_at"] = asyncio.get_running_loop().time()
            state["transitioning"] = False
            state["recovering"] = False

        if old_temp_dir and old_temp_dir != temp_dir:
            await _cleanup_temp_dir(old_temp_dir)

        LOGGER.info(
            "Voice-chat audio started in chat %s: %s (generation %s)",
            chat_id,
            track.get("title", "Unknown track"),
            state["generation"],
        )
    except BaseException:
        if downloaded:
            await _cleanup_temp_dir(downloaded.get("temp_dir"))
        # Keep the transition reserved until the caller either tries the next
        # queued item or clears the failed initial/replay request.
        raise


async def replay_current(chat_id: int) -> bool:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current") or state.get("transitioning"):
            return False
        track = dict(state["current"])
        state["transitioning"] = True

    try:
        await _start_track(chat_id, track)
        return True
    except Exception:
        async with _chat_locks[chat_id]:
            if _sessions.get(chat_id) is state:
                state["transitioning"] = False
        raise


async def advance_queue(
    chat_id: int,
    *,
    announce: bool = True,
    force_skip: bool = False,
    expected_generation: int | None = None,
    natural_end: bool = False,
) -> bool:
    """Advance without holding the chat lock during downloads or radio lookups."""
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current") or state.get("transitioning"):
            return False

        generation = int(state.get("generation", 0))
        if expected_generation is not None and generation != expected_generation:
            LOGGER.debug(
                "Ignoring stale queue transition in %s (expected %s, current %s)",
                chat_id, expected_generation, generation,
            )
            return False

        now = asyncio.get_running_loop().time()
        if natural_end and now - float(state.get("started_at", 0.0)) < STREAM_END_GUARD_SECONDS:
            LOGGER.debug("Ignoring a likely stale stream-end event in chat %s.", chat_id)
            return False

        state["transitioning"] = True
        if force_skip:
            state["loop_remaining"] = 0
        remaining = int(state.get("loop_remaining", 0))
        if not force_skip and remaining > 0:
            state["loop_remaining"] = remaining - 1
            next_track = dict(state["current"])
        else:
            next_track = state["queue"].pop(0) if state.get("queue") else None
        state_ref = state

    autoplay_attempted = False
    while True:
        while next_track is not None:
            try:
                await _start_track(chat_id, next_track)
                if announce:
                    await _send_now_playing(chat_id, next_track)
                return True
            except Exception:
                LOGGER.exception("Could not start the next track in %s", chat_id)
                async with _chat_locks[chat_id]:
                    live_state = _sessions.get(chat_id)
                    if live_state is not state_ref:
                        return False
                    next_track = (
                        live_state["queue"].pop(0)
                        if live_state.get("queue")
                        else None
                    )

        # Prefer a manually queued track if one arrived while the last
        # download was finishing. Otherwise refill from YouTube Music radio.
        async with _chat_locks[chat_id]:
            live_state = _sessions.get(chat_id)
            if live_state is not state_ref:
                return False
            if live_state.get("queue"):
                next_track = live_state["queue"].pop(0)
                should_autoplay = False
                seed_track = {}
                seen_ids = []
            else:
                next_track = None
                should_autoplay = (
                    bool(live_state.get("autoplay")) and not autoplay_attempted
                )
                seed_track = dict(live_state.get("current") or {})
                seen_ids = list(live_state.get("autoplay_seen") or [])

        if next_track is not None:
            continue

        if should_autoplay:
            autoplay_attempted = True
            try:
                auto_tracks = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_related_tracks_sync,
                        seed_track,
                        5,
                        seen_ids,
                    ),
                    timeout=20,
                )
            except asyncio.TimeoutError:
                LOGGER.warning("Auto Mix lookup timed out in chat %s.", chat_id)
                auto_tracks = []
            except Exception:
                LOGGER.exception("Auto Mix lookup failed in chat %s.", chat_id)
                auto_tracks = []

            async with _chat_locks[chat_id]:
                live_state = _sessions.get(chat_id)
                if live_state is not state_ref:
                    return False
                # Respect a user switching Auto Mix off while lookup was running.
                if live_state.get("autoplay") and auto_tracks:
                    live_state["queue"].extend(auto_tracks)
                    live_state["autoplay_seen"] = list(dict.fromkeys(
                        seen_ids + [
                            str(track.get("video_id"))
                            for track in auto_tracks
                            if track.get("video_id")
                        ]
                    ))[-60:]
                    LOGGER.info(
                        "Auto Mix queued %s related tracks in chat %s.",
                        len(auto_tracks),
                        chat_id,
                    )
                next_track = (
                    live_state["queue"].pop(0)
                    if live_state.get("queue")
                    else None
                )
            if next_track is not None:
                continue

        break

    async with _chat_locks[chat_id]:
        if _sessions.get(chat_id) is state_ref:
            await _stop_chat_locked(chat_id)
        else:
            return False
    if announce:
        await _send_chat_message(
            chat_id,
            "🏁 <b>Queue finished.</b> MikasaVerse left the voice chat.",
        )
    return True

async def skip_current(chat_id: int) -> bool:
    # Capture before waiting for the lock so a simultaneous natural-end event
    # cannot make this command skip the newly-started next song as well.
    state = _sessions.get(chat_id)
    if not state or not state.get("current"):
        return False
    generation = int(state.get("generation", 0))
    return await advance_queue(
        chat_id,
        announce=True,
        force_skip=True,
        expected_generation=generation,
    )


async def pause_chat(chat_id: int) -> bool:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current") or state.get("transitioning") or _calls is None:
            return False
        await _calls.pause(chat_id)
        state["paused"] = True
        return True


async def resume_chat(chat_id: int) -> bool:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current") or state.get("transitioning") or _calls is None:
            return False
        await _calls.resume(chat_id)
        state["paused"] = False
        return True


async def change_volume(chat_id: int, level: int) -> bool:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current") or _calls is None:
            return False
        await _calls.change_volume_call(chat_id, level)
        state["volume"] = level
        return True


async def set_loop_remaining(chat_id: int, count: int) -> bool:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current"):
            return False
        state["loop_remaining"] = count
        return True


async def toggle_loop(chat_id: int) -> int | None:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current"):
            return None
        state["loop_remaining"] = 0 if state.get("loop_remaining", 0) else 1
        return int(state["loop_remaining"])


async def toggle_autoplay(chat_id: int) -> bool | None:
    """Enable or disable automatic YouTube Music radio when the queue ends."""
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current"):
            return None
        enabled = not bool(state.get("autoplay", False))
        state["autoplay"] = enabled
        if enabled:
            state["autoplay_seen"] = []
        return enabled


async def shuffle_queue(chat_id: int) -> int:
    import random

    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state:
            return 0
        random.shuffle(state["queue"])
        return len(state["queue"])


async def clear_queue(chat_id: int) -> int:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state:
            return 0
        removed = len(state["queue"])
        state["queue"].clear()
        return removed


async def remove_queued_track(chat_id: int, position: int) -> dict | None:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        queue = state.get("queue", []) if state else []
        if not 1 <= position <= len(queue):
            return None
        return queue.pop(position - 1)


async def move_queued_track(chat_id: int, source: int, destination: int) -> bool:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        queue = state.get("queue", []) if state else []
        if not 1 <= source <= len(queue) or not 1 <= destination <= len(queue):
            return False
        track = queue.pop(source - 1)
        queue.insert(destination - 1, track)
        return True


async def _on_stream_end(_: PyTgCalls, update: StreamEnded) -> None:
    state = _sessions.get(update.chat_id)
    if not state or not state.get("current"):
        return
    generation = int(state.get("generation", 0))
    await advance_queue(
        update.chat_id,
        announce=True,
        expected_generation=generation,
        natural_end=True,
    )


async def _discard_session_after_call_end(chat_id: int) -> None:
    async with _chat_locks[chat_id]:
        await _stop_chat_locked(chat_id, leave_call=False)


async def _recover_disconnected_chat(chat_id: int, generation: int, rejoin_group: bool) -> None:
    """Try one bounded recovery when Telegram removes the assistant from a call."""
    await asyncio.sleep(1.0)
    error = None
    try:
        async with _chat_locks[chat_id]:
            state = _sessions.get(chat_id)
            if (
                not state
                or int(state.get("generation", 0)) != generation
                or not state.get("current")
                or not state.get("recovering")
                or _calls is None
            ):
                return

            if rejoin_group:
                _resolved_chat_peers.discard(chat_id)
                await _ensure_chat_peer(chat_id)

            path = state.get("active_path")
            if not path or not os.path.isfile(path):
                raise RuntimeError("The cached audio file is no longer available.")

            await _calls.play(
                chat_id,
                _media_stream(path, state.get("active_kind", "audio")),
                GroupCallConfig(auto_start=False),
            )
            state["generation"] = generation + 1
            state["started_at"] = asyncio.get_running_loop().time()
            state["paused"] = False
            state["recovering"] = False
            state["transitioning"] = False
            title = state["current"].get("title", "Current track")
            LOGGER.warning("Recovered music assistant in chat %s.", chat_id)
        await _send_chat_message(
            chat_id,
            f"🔄 <b>Music connection restored.</b>\n🎵 Restarted: {escape(title)}",
            reply_markup=player_keyboard(
                chat_id,
                autoplay=bool(state.get("autoplay", False)),
            ),
        )
        return
    except Exception as exc:
        error = exc
        LOGGER.exception("Music assistant recovery failed in chat %s.", chat_id)

    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if state and int(state.get("generation", 0)) == generation:
            await _stop_chat_locked(chat_id)
    await _send_chat_message(
        chat_id,
        "⚠️ <b>Music connection was interrupted.</b> "
        "Please turn on the group voice chat and use <code>/play</code> again.",
    )


async def _on_chat_update(_: PyTgCalls, update) -> None:
    """Handle assistant kicked/removed or voice chat closed by Telegram."""
    try:
        from pytgcalls.types import ChatUpdate
    except ImportError:
        return

    status = update.status
    if status & ChatUpdate.Status.LEFT_GROUP:
        _resolved_chat_peers.discard(update.chat_id)

    state = _sessions.get(update.chat_id)
    if not state or not state.get("current"):
        return

    if status & ChatUpdate.Status.CLOSED_VOICE_CHAT:
        LOGGER.info("Voice chat closed in %s; clearing the music session.", update.chat_id)
        await _discard_session_after_call_end(update.chat_id)
        await _send_chat_message(
            update.chat_id,
            "🎙️ <b>Voice chat ended.</b> Music queue has been stopped.",
        )
        return

    if status & (ChatUpdate.Status.KICKED | ChatUpdate.Status.LEFT_GROUP):
        async with _chat_locks[update.chat_id]:
            state = _sessions.get(update.chat_id)
            if not state or state.get("recovering"):
                return
            state["recovering"] = True
            generation = int(state.get("generation", 0))
        LOGGER.warning("Assistant disconnected from chat %s; attempting recovery.", update.chat_id)
        asyncio.create_task(
            _recover_disconnected_chat(
                update.chat_id,
                generation,
                bool(status & ChatUpdate.Status.LEFT_GROUP),
            )
        )
        return

    if status & (ChatUpdate.Status.DISCARDED_CALL | ChatUpdate.Status.BUSY_CALL):
        await _discard_session_after_call_end(update.chat_id)
        await _send_chat_message(
            update.chat_id,
            "⚠️ <b>Music call disconnected.</b> Start the group voice chat and use <code>/play</code> again.",
        )


async def _on_participant_update(_: PyTgCalls, update) -> None:
    """Recover when Telegram removes the assistant account from the call."""
    if _music_client is None or not _sessions.get(update.chat_id):
        return
    try:
        me = getattr(_music_client, "me", None) or await _music_client.get_me()
        if not me or update.participant.user_id != me.id:
            return
        from pytgcalls.types import GroupCallParticipant

        if not (
            update.action
            & (GroupCallParticipant.Action.KICKED | GroupCallParticipant.Action.LEFT)
        ):
            return
    except Exception:
        LOGGER.debug("Could not identify assistant participant update.", exc_info=True)
        return

    async with _chat_locks[update.chat_id]:
        state = _sessions.get(update.chat_id)
        if not state or not state.get("current") or state.get("recovering"):
            return
        state["recovering"] = True
        generation = int(state.get("generation", 0))

    LOGGER.warning("Telegram removed the assistant from the call in %s.", update.chat_id)
    asyncio.create_task(
        _recover_disconnected_chat(update.chat_id, generation, rejoin_group=False)
    )


async def start_music() -> None:
    """Start the optional PyrogramMod + PyTgCalls assistant."""
    global _music_client, _calls, _startup_status

    _startup_status = "starting"
    credentials, status, missing = get_music_credentials()
    if credentials is None:
        _startup_status = status
        if status == "missing_credentials":
            LOGGER.warning(
                "Music assistant disabled; missing Render variables: %s",
                ", ".join(missing),
            )
        elif status == "invalid_api_id":
            LOGGER.error("Music assistant disabled: MUSIC_API_ID must be numeric.")
        return

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        _startup_status = "missing_ffmpeg"
        LOGGER.error("Music assistant disabled because ffmpeg/ffprobe are missing.")
        return

    api_id, api_hash, session = credentials
    try:
        _music_client = PyrogramClient(
            "mikasa_music_assistant",
            api_id=api_id,
            api_hash=api_hash,
            session_string=session,
        )
        await _music_client.start()
        _calls = PyTgCalls(_music_client)

        @_calls.on_update(call_filters.stream_end(StreamEnded.Type.AUDIO))
        async def _stream_end_handler(client: PyTgCalls, event: StreamEnded):
            await _on_stream_end(client, event)

        try:
            from pytgcalls.types import ChatUpdate, GroupCallParticipant

            left_call = getattr(ChatUpdate.Status, "LEFT_CALL", None)
            chat_update_filter = getattr(call_filters, "chat_update", None)
            if left_call is not None and chat_update_filter is not None:
                @_calls.on_update(chat_update_filter(left_call))
                async def _chat_update_handler(client: PyTgCalls, event):
                    await _on_chat_update(client, event)

            participant_filter = getattr(call_filters, "call_participant", None)
            if participant_filter is not None:
                removed_actions = (
                    GroupCallParticipant.Action.KICKED
                    | GroupCallParticipant.Action.LEFT
                )
                @_calls.on_update(participant_filter(removed_actions))
                async def _participant_handler(client: PyTgCalls, event):
                    await _on_participant_update(client, event)
            else:
                LOGGER.warning("PyTgCalls participant filter is unavailable; kick recovery is disabled.")
        except ImportError:
            LOGGER.warning("PyTgCalls lifecycle event types are unavailable; recovery events are disabled.")

        await _calls.start()
        _startup_status = "ready"
        LOGGER.info("MikasaVerse voice-chat music assistant started.")
    except Exception:
        _startup_status = "startup_failed"
        LOGGER.exception("Could not start the MikasaVerse music assistant.")
        if _music_client is not None:
            try:
                await _music_client.stop()
            except Exception:
                pass
        _calls = None
        _music_client = None


async def stop_music() -> None:
    """Close all calls and delete downloaded audio on service shutdown."""
    global _music_client, _calls, _startup_status
    for chat_id in list(_sessions):
        await stop_chat(chat_id)
    _resolved_chat_peers.clear()
    if _music_client is not None:
        try:
            await _music_client.stop()
        except Exception:
            LOGGER.exception("Error while stopping Pyrogram music assistant.")
    _calls = None
    _music_client = None
    _startup_status = "stopped"
