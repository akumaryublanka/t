"""Voice-chat music player for MikasaVerse.

Music is optional until a dedicated Telegram user session is configured.
The regular Bot API client continues to handle commands and replies.
"""
import asyncio
import logging
import os
import shutil
from collections import defaultdict
from html import escape

from pyrogram import Client as PyrogramClient
import pyrogram.errors as pyrogram_errors

# py-tgcalls expects legacy RPC error spellings that stock Pyrogram 2.0.106
# does not export. Keep compatibility without changing the user session/client.
if not hasattr(pyrogram_errors, "GroupcallForbidden"):
    pyrogram_errors.GroupcallForbidden = getattr(
        pyrogram_errors, "GroupCallForbidden", type("GroupcallForbidden", (Exception,), {})
    )
if not hasattr(pyrogram_errors, "GroupcallInvalid"):
    pyrogram_errors.GroupcallInvalid = getattr(
        pyrogram_errors, "GroupCallInvalid", type("GroupcallInvalid", (Exception,), {})
    )

from pytgcalls import PyTgCalls
from pytgcalls import filters as call_filters
from pytgcalls.types import GroupCallConfig, StreamEnded
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler
from yt_dlp import YoutubeDL

from waifu import application

LOGGER = logging.getLogger(__name__)

_music_client: PyrogramClient | None = None
_calls: PyTgCalls | None = None
_sessions: dict[int, dict] = {}
_chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _configured() -> tuple[int, str, str] | None:
    api_id = os.environ.get("MUSIC_API_ID", "").strip()
    api_hash = os.environ.get("MUSIC_API_HASH", "").strip()
    session = os.environ.get("MUSIC_SESSION_STRING", "").strip()
    if not (api_id and api_hash and session):
        return None
    try:
        return int(api_id), api_hash, session
    except ValueError:
        LOGGER.error("MUSIC_API_ID must be a numeric Telegram API ID.")
        return None


def _extract_info_sync(query: str) -> dict:
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


def _stream_url_sync(source: str) -> str:
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


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "Live / unknown"
    minutes, seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _group_only(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in ("group", "supergroup"))


async def _reply(update: Update, text: str) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def _require_music(update: Update) -> bool:
    if not _group_only(update):
        await _reply(update, "🎵 Music commands work inside a group voice chat.")
        return False
    if _calls is None:
        await _reply(
            update,
            "⚙️ <b>Music assistant is not configured yet.</b>\n"
            "The owner must add <code>MUSIC_API_ID</code>, "
            "<code>MUSIC_API_HASH</code> and <code>MUSIC_SESSION_STRING</code> "
            "to Render Environment.",
        )
        return False
    return True


async def _start_track(chat_id: int, track: dict) -> None:
    if _calls is None:
        raise RuntimeError("Music assistant is not connected.")
    stream_url = await asyncio.to_thread(_stream_url_sync, track["source"])
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
                        f"⏱️ {_duration(next_track.get('duration'))}",
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
            await _send_chat_message(chat_id, "🏁 <b>Queue finished.</b> MikasaVerse left the voice chat.")


async def _send_chat_message(chat_id: int, text: str) -> None:
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        LOGGER.exception("Could not send music status message to %s", chat_id)


async def play(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    query = " ".join(context.args).strip()
    if not query:
        await _reply(update, "Usage: <code>/play song name or YouTube link</code>")
        return
    if len(query) > 500:
        await _reply(update, "❌ Search text or URL is too long.")
        return

    await _reply(update, "🔎 <i>Finding your track…</i>")
    try:
        track = await asyncio.to_thread(_extract_info_sync, query)
    except Exception as exc:
        LOGGER.warning("Music search failed: %s", exc)
        await _reply(update, "❌ I couldn't find a playable track. Try another title or link.")
        return

    track["requested_by"] = update.effective_user.first_name if update.effective_user else "Unknown"

    chat_id = update.effective_chat.id
    async with _chat_locks[chat_id]:
        state = _sessions.setdefault(chat_id, {"current": None, "queue": [], "volume": 100})
        if state["current"] is None:
            try:
                await _start_track(chat_id, track)
                await _reply(
                    update,
                    f"🎶 <b>NOW PLAYING</b>\n"
                    f"🎧 {escape(track['title'])}\n"
                    f"⏱️ {_duration(track.get('duration'))}\n"
                    f"👤 Requested by {escape(track['requested_by'])}",
                )
            except Exception as exc:
                _sessions.pop(chat_id, None)
                LOGGER.exception("Could not start music in %s", chat_id)
                await _reply(
                    update,
                    "❌ Couldn't join or start the voice chat. Make sure the assistant account "
                    "is in this group and has permission to manage video chats.\n"
                    f"<code>{escape(str(exc)[:240])}</code>",
                )
        else:
            state["queue"].append(track)
            position = len(state["queue"])
            await _reply(
                update,
                f"➕ <b>Added to queue</b>\n"
                f"🎵 {escape(track['title'])}\n"
                f"📍 Position: <b>{position}</b>\n"
                f"⏱️ {_duration(track.get('duration'))}",
            )


async def pause(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    try:
        await _calls.pause(chat_id)
        await _reply(update, "⏸️ Music paused.")
    except Exception:
        await _reply(update, "❌ No active music stream to pause.")


async def resume(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    try:
        await _calls.resume(chat_id)
        await _reply(update, "▶️ Music resumed.")
    except Exception:
        await _reply(update, "❌ No paused music stream to resume.")


async def skip(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    state = _sessions.get(chat_id)
    if not state or not state.get("current"):
        await _reply(update, "ℹ️ Nothing is playing right now.")
        return
    await _reply(update, "⏭️ Skipping the current track…")
    await _advance_queue(chat_id, announce=True)


async def end(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    async with _chat_locks[chat_id]:
        _sessions.pop(chat_id, None)
        try:
            await _calls.leave_call(chat_id)
        except Exception:
            pass
    await _reply(update, "⏹️ <b>Music stopped.</b> Queue cleared and assistant left the voice chat.")


async def queue(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = _sessions.get(update.effective_chat.id)
    if not state or not state.get("current"):
        await _reply(update, "🎵 The music queue is empty.")
        return

    current = state["current"]
    lines = [
        "🎶 <b>MIKASAVERSE MUSIC QUEUE</b>",
        "",
        f"▶️ <b>Now:</b> {escape(current['title'])}  <i>({_duration(current.get('duration'))})</i>",
    ]
    for index, track in enumerate(state["queue"][:10], start=1):
        lines.append(
            f"{index}. {escape(track['title'])}  "
            f"<i>({_duration(track.get('duration'))})</i>"
        )
    if len(state["queue"]) > 10:
        lines.append(f"…and {len(state['queue']) - 10} more track(s)")
    await _reply(update, "\n".join(lines))


async def volume(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await _reply(update, "Usage: <code>/volume 1-200</code>")
        return
    level = int(context.args[0])
    if not 1 <= level <= 200:
        await _reply(update, "❌ Volume must be between 1 and 200.")
        return
    chat_id = update.effective_chat.id
    try:
        await _calls.change_volume_call(chat_id, level)
        _sessions.setdefault(chat_id, {"current": None, "queue": [], "volume": 100})["volume"] = level
        await _reply(update, f"🔊 Volume set to <b>{level}%</b>.")
    except Exception:
        await _reply(update, "❌ Couldn't change volume. Start a track first.")


async def _on_stream_end(_: PyTgCalls, update: StreamEnded) -> None:
    await _advance_queue(update.chat_id, announce=True)


async def start_music() -> None:
    """Start the optional Pyrogram + PyTgCalls assistant."""
    global _music_client, _calls

    credentials = _configured()
    if credentials is None:
        LOGGER.warning(
            "Voice-chat music is idle: set MUSIC_API_ID, MUSIC_API_HASH and "
            "MUSIC_SESSION_STRING in the service environment to enable it."
        )
        return
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        LOGGER.error("Voice-chat music is disabled because ffmpeg/ffprobe are missing.")
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
        LOGGER.info("MikasaVerse voice-chat music assistant started.")
    except Exception:
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
    global _music_client, _calls
    for chat_id in list(_sessions):
        try:
            if _calls is not None:
                await _calls.leave_call(chat_id)
        except Exception:
            LOGGER.debug("Could not leave voice chat %s during shutdown.", chat_id, exc_info=True)
    _sessions.clear()

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


application.add_handler(CommandHandler("play", play, block=False))
application.add_handler(CommandHandler("pause", pause, block=False))
application.add_handler(CommandHandler("resume", resume, block=False))
application.add_handler(CommandHandler("skip", skip, block=False))
application.add_handler(CommandHandler("end", end, block=False))
application.add_handler(CommandHandler("queue", queue, block=False))
application.add_handler(CommandHandler("volume", volume, block=False))
