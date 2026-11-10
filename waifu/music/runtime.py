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
from waifu.music.downloader import stream_url_sync
from waifu.music.formatting import duration

LOGGER = logging.getLogger("waifu.music")

_music_client: PyrogramClient | None = None
_calls: PyTgCalls | None = None
_sessions: dict[int, dict] = {}
_chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_resolved_chat_peers: set[int] = set()
_startup_status = "not_started"


def get_calls() -> PyTgCalls | None:
    return _calls


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


async def _send_chat_message(chat_id: int, text: str) -> None:
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        LOGGER.exception("Could not send music status message to %s", chat_id)


async def _ensure_chat_peer(chat_id: int) -> None:
    """Populate this session's access-hash cache for the target group.

    Session strings use in-memory storage. A numeric supergroup ID alone is
    insufficient for MTProto calls until its peer/access hash has been seen.
    get_dialogs() fetches the user's dialogs and populates Pyrogram's peer DB.
    """
    if _music_client is None:
        raise RuntimeError("The music assistant Telegram session is not running.")
    if chat_id in _resolved_chat_peers:
        return

    async for dialog in _music_client.get_dialogs():
        if dialog.chat and dialog.chat.id == chat_id:
            _resolved_chat_peers.add(chat_id)
            LOGGER.info("Music assistant resolved group peer %s from its dialogs.", chat_id)
            return

    raise RuntimeError(
        "The music assistant account could not find this group in its Telegram dialogs. "
        "Add the assistant user account to this group, open the group once with that account, "
        "and make it an admin with Manage Video Chats permission."
    )


async def _start_track(chat_id: int, track: dict) -> None:
    if _calls is None:
        raise RuntimeError("Music assistant is not connected.")
    await _ensure_chat_peer(chat_id)
    stream_url = await asyncio.to_thread(stream_url_sync, track["source"])
    await _calls.play(
        chat_id,
        stream_url,
        GroupCallConfig(auto_start=True),
    )
    _sessions.setdefault(chat_id, {"current": None, "queue": [], "volume": 100})
    _sessions[chat_id]["current"] = track


async def _advance_queue(chat_id: int, *, announce: bool = True) -> None:
    async with _chat_locks[chat_id]:
        state = _sessions.get(chat_id)
        if not state:
            return

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
                    )
                return
            except Exception:
                LOGGER.exception("Could not start the next queued track in %s", chat_id)
                continue

        _sessions.pop(chat_id, None)
        if _calls is not None:
            try:
                await _calls.leave_call(chat_id)
            except Exception:
                LOGGER.debug("Voice chat was already left in %s.", chat_id, exc_info=True)
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
        if _calls is not None:
            try:
                await _calls.stop()
            except Exception:
                pass
        if _music_client is not None:
            try:
                await _music_client.stop()
            except Exception:
                pass
        _calls = None
        _music_client = None


async def stop_music() -> None:
    """Close the voice-chat assistant cleanly during service shutdown."""
    global _music_client, _calls, _startup_status

    for chat_id in list(_sessions):
        try:
            if _calls is not None:
                await _calls.leave_call(chat_id)
        except Exception:
            LOGGER.debug(
                "Could not leave voice chat %s during shutdown.",
                chat_id,
                exc_info=True,
            )
    _sessions.clear()
    _resolved_chat_peers.clear()

    if _calls is not None:
        try:
            await _calls.stop()
        except Exception:
            LOGGER.exception("Error while stopping PyTgCalls.")
    if _music_client is not None:
        try:
            await _music_client.stop()
        except Exception:
            LOGGER.exception("Error while stopping Pyrogram music assistant.")
    _calls = None
    _music_client = None
    _startup_status = "stopped"
