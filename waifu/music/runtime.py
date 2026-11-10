"""Voice-chat assistant lifecycle, stream state, and queue management."""

import asyncio
import logging
import shutil
from collections import defaultdict
from html import escape

from pyrogram import Client as PyrogramClient
from pytgcalls import PyTgCalls
from pytgcalls import filters as call_filters
from pytgcalls.types import GroupCallConfig, StreamEnded

from waifu import application
from waifu.music.config import get_music_credentials
from waifu.music.downloader import download_track_sync
from waifu.music.formatting import duration, player_keyboard

LOGGER = logging.getLogger("waifu.music")

_music_client: PyrogramClient | None = None
_calls: PyTgCalls | None = None
_sessions: dict[int, dict] = {}
_chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_resolved_chat_peers: set[int] = set()
_startup_status = "not_started"


def get_calls() -> PyTgCalls | None:
    return _calls


def get_music_client() -> PyrogramClient | None:
    return _music_client


async def _cleanup_temp_dir(path: str | None) -> None:
    if path:
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)


async def stop_chat(chat_id: int) -> None:
    """Stop one group's stream and release its downloaded audio."""
    state = _sessions.pop(chat_id, None)
    temp_dir = state.get("active_temp_dir") if state else None
    try:
        if _calls is not None:
            await _calls.leave_call(chat_id)
    except Exception:
        LOGGER.debug("Voice chat was already left in %s.", chat_id, exc_info=True)
    finally:
        await _cleanup_temp_dir(temp_dir)


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


async def _find_chat_in_dialogs(chat_id: int) -> bool:
    """Resolve a group peer from the assistant account's Telegram dialogs."""
    if _music_client is None:
        return False
    async for dialog in _music_client.get_dialogs():
        if dialog.chat and dialog.chat.id == chat_id:
            _resolved_chat_peers.add(chat_id)
            LOGGER.info("Music assistant resolved group peer %s from its dialogs.", chat_id)
            return True
    return False


async def _ensure_chat_peer(chat_id: int) -> None:
    """Join the current group using a temporary invite created by the main bot.

    The main bot must be an admin with Invite Users permission. A one-use link
    is created only when the assistant is not already a participant and is
    revoked immediately after the join attempt.
    """
    if _music_client is None:
        raise RuntimeError("The music assistant Telegram session is not running.")
    if chat_id in _resolved_chat_peers:
        return
    if await _find_chat_in_dialogs(chat_id):
        return

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

        if joined_chat_id == chat_id or await _find_chat_in_dialogs(chat_id):
            _resolved_chat_peers.add(chat_id)
            LOGGER.info("Music assistant automatically joined group %s.", chat_id)
            return

        raise RuntimeError("Telegram did not confirm that the assistant joined this group.")
    except Exception as exc:
        try:
            if await _find_chat_in_dialogs(chat_id):
                return
        except Exception:
            LOGGER.debug("Could not refresh music assistant dialogs after auto-join failure.", exc_info=True)
        raise RuntimeError(
            "Automatic group join failed. Make sure the main MikasaVerse bot is an admin "
            "with Invite Users permission in this group, and that the group does not require "
            "manual approval for new members."
        ) from exc
    finally:
        if invite_link:
            try:
                await application.bot.revoke_chat_invite_link(
                    chat_id=chat_id,
                    invite_link=invite_link,
                )
            except Exception:
                LOGGER.warning("Could not revoke the temporary music assistant invite link in %s.", chat_id)


async def _start_track(chat_id: int, track: dict) -> None:
    """Download locally and pass the file to PyTgCalls/FFmpeg."""
    if _calls is None:
        raise RuntimeError("Music assistant is not connected.")
    await _ensure_chat_peer(chat_id)
    downloaded = await asyncio.to_thread(download_track_sync, track["source"])
    new_temp_dir = downloaded["temp_dir"]
    try:
        # PyTgCalls 2.3.x accepts a local path and probes it with FFmpeg.
        await _calls.play(chat_id, downloaded["path"], GroupCallConfig(auto_start=False))
    except Exception:
        await _cleanup_temp_dir(new_temp_dir)
        raise

    state = _sessions.setdefault(
        chat_id,
        {"current": None, "queue": [], "volume": 100,
         "loop_remaining": 0, "paused": False, "active_temp_dir": None},
    )
    previous_temp_dir = state.get("active_temp_dir")
    state.setdefault("loop_remaining", 0)
    state.setdefault("paused", False)
    state["current"] = track
    state["paused"] = False
    state["active_temp_dir"] = new_temp_dir
    if previous_temp_dir and previous_temp_dir != new_temp_dir:
        await _cleanup_temp_dir(previous_temp_dir)
    LOGGER.info("Local audio stream started in chat %s: %s",
                chat_id, track.get("title", "Unknown track"))

async def replay_current(chat_id: int) -> bool:
    """Restart the current track without changing the upcoming queue."""
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state or not state.get("current"):
            return False
        await _start_track(chat_id, state["current"])
        return True


async def _advance_queue(
    chat_id: int,
    *,
    announce: bool = True,
    force_skip: bool = False,
) -> None:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state:
            return

        if force_skip:
            state["loop_remaining"] = 0
        else:
            current = state.get("current")
            remaining = int(state.get("loop_remaining", 0))
            if current and remaining > 0:
                state["loop_remaining"] = remaining - 1
                try:
                    await _start_track(chat_id, current)
                    if announce:
                        await _send_chat_message(
                            chat_id,
                            f"🔁 <b>REPEATING TRACK</b>\n"
                            f"🎵 {escape(current['title'])}\n"
                            f"⏱️ {duration(current.get('duration'))}",
                            reply_markup=player_keyboard(chat_id),
                        )
                    return
                except Exception:
                    LOGGER.exception("Could not repeat the current track in %s", chat_id)

        while state["queue"]:
            next_track = state["queue"].pop(0)
            try:
                await _start_track(chat_id, next_track)
                if announce:
                    await _send_chat_message(
                        chat_id,
                        f"⏭️ <b>NOW PLAYING</b>\n"
                        f"🎵 {escape(next_track['title'])}\n"
                        f"⏱️ {duration(next_track.get('duration'))}",
                        reply_markup=player_keyboard(chat_id),
                    )
                return
            except Exception:
                LOGGER.exception("Could not start the next queued track in %s", chat_id)
                continue

        finished_state = _sessions.pop(chat_id, None)
        finished_temp_dir = finished_state.get("active_temp_dir") if finished_state else None
        try:
            if _calls is not None:
                await _calls.leave_call(chat_id)
        except Exception:
            LOGGER.debug("Voice chat was already left in %s.", chat_id, exc_info=True)
        finally:
            await _cleanup_temp_dir(finished_temp_dir)
        if announce:
            await _send_chat_message(
                chat_id,
                "🏁 <b>Queue finished.</b> MikasaVerse left the voice chat.",
            )


async def _on_stream_end(_: PyTgCalls, update: StreamEnded) -> None:
    await _advance_queue(update.chat_id, announce=True)


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

        @_calls.on_update(call_filters.stream_end())
        async def _stream_end_handler(client: PyTgCalls, event: StreamEnded):
            await _on_stream_end(client, event)

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
    for chat_id, state in list(_sessions.items()):
        try:
            if _calls is not None:
                await _calls.leave_call(chat_id)
        except Exception:
            LOGGER.debug("Could not leave voice chat %s during shutdown.", chat_id, exc_info=True)
        finally:
            await _cleanup_temp_dir(state.get("active_temp_dir"))
    _sessions.clear()
    _resolved_chat_peers.clear()
    if _music_client is not None:
        try:
            await _music_client.stop()
        except Exception:
            LOGGER.exception("Error while stopping Pyrogram music assistant.")
    _calls = None
    _music_client = None
    _startup_status = "stopped"
