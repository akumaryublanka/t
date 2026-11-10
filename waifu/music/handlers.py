"""Telegram commands for the MikasaVerse voice-chat music player."""

import asyncio
from html import escape

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

from waifu import application
from waifu.music import runtime
from waifu.music.downloader import extract_info_sync
from waifu.music.formatting import duration, group_only, reply


async def _require_music(update: Update) -> bool:
    if not group_only(update):
        await reply(update, "🎵 Music commands work inside a group voice chat.")
        return False
    if runtime.get_calls() is None:
        await reply(update, runtime.unavailable_message())
        return False
    return True


async def play(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    query = " ".join(context.args).strip()
    if not query:
        await reply(update, "Usage: <code>/play song name or YouTube link</code>")
        return
    if len(query) > 500:
        await reply(update, "❌ Search text or URL is too long.")
        return

    await reply(update, "🔎 <i>Finding your track…</i>")
    try:
        track = await asyncio.to_thread(extract_info_sync, query)
    except Exception as exc:
        runtime.LOGGER.warning("Music search failed: %s", exc)
        await reply(update, "❌ I couldn't find a playable track. Try another title or link.")
        return

    track["requested_by"] = (
        update.effective_user.first_name if update.effective_user else "Unknown"
    )

    chat_id = update.effective_chat.id
    async with runtime._chat_locks[chat_id]:
        state = runtime._sessions.setdefault(
            chat_id,
            {"current": None, "queue": [], "volume": 100},
        )
        if state["current"] is None:
            try:
                await runtime._start_track(chat_id, track)
                await reply(
                    update,
                    f"🎶 <b>NOW PLAYING</b>\n"
                    f"🎧 {escape(track['title'])}\n"
                    f"⏱️ {duration(track.get('duration'))}\n"
                    f"👤 Requested by {escape(track['requested_by'])}",
                )
            except Exception as exc:
                runtime._sessions.pop(chat_id, None)
                runtime.LOGGER.exception("Could not start music in %s", chat_id)
                error_text = str(exc).lower()
                error_name = type(exc).__name__.lower()
                if "noactivegroupcall" in error_name or "no active group call" in error_text:
                    await reply(
                        update,
                        "🎙️ <b>Voice Chat abhi OFF hai!</b>\n"
                        "Pehle group mein Voice Chat ON karo, phir <code>/play</code> dobara bhejo. "
                        "Assistant khud join karke music play karega.",
                    )
                elif any(marker in error_text for marker in ("sign in to confirm", "cookies", "youtube")):
                    await reply(
                        update,
                        "🍪 <b>YouTube authentication failed.</b>\n"
                        "Assistant group mein join ho gaya, lekin YouTube ne playback stream block kar di.\n"
                        "Owner ko Render Secret Files mein valid Netscape-format YouTube cookies add karke "
                        "<code>YTDLP_COOKIES_FILE</code> ka path set karna hoga.\n"
                        f"<code>{escape(str(exc)[:240])}</code>",
                    )
                else:
                    await reply(
                        update,
                        "❌ <b>Music playback start nahi ho paya.</b> Assistant join ho sakta hai, "
                        "lekin voice chat playback ya stream start karte waqt error aaya.\n"
                        f"<code>{escape(str(exc)[:240])}</code>",
                    )
        else:
            state["queue"].append(track)
            position = len(state["queue"])
            await reply(
                update,
                f"➕ <b>Added to queue</b>\n"
                f"🎵 {escape(track['title'])}\n"
                f"📍 Position: <b>{position}</b>\n"
                f"⏱️ {duration(track.get('duration'))}",
            )


async def pause(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    await runtime.get_calls().pause(update.effective_chat.id)
    await reply(update, "⏸️ Music paused.")


async def resume(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    await runtime.get_calls().resume(update.effective_chat.id)
    await reply(update, "▶️ Music resumed.")


async def skip(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    state = runtime._sessions.get(chat_id)
    if not state or not state.get("current"):
        await reply(update, "ℹ️ Nothing is playing right now.")
        return
    await reply(update, "⏭️ Skipping the current track…")
    await runtime._advance_queue(chat_id, announce=True)


async def end(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    async with runtime._chat_locks[chat_id]:
        runtime._sessions.pop(chat_id, None)
        try:
            await runtime.get_calls().leave_call(chat_id)
        except Exception:
            pass
    await reply(
        update,
        "⏹️ <b>Music stopped.</b> Queue cleared and assistant left the voice chat.",
    )


async def queue(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = runtime._sessions.get(update.effective_chat.id)
    if not state or not state.get("current"):
        await reply(update, "🎵 The music queue is empty.")
        return

    current = state["current"]
    lines = [
        "🎶 <b>MIKASAVERSE MUSIC QUEUE</b>",
        "",
        f"▶️ <b>Now:</b> {escape(current['title'])}  "
        f"<i>({duration(current.get('duration'))})</i>",
    ]
    for index, track in enumerate(state["queue"][:10], start=1):
        lines.append(
            f"{index}. {escape(track['title'])}  "
            f"<i>({duration(track.get('duration'))})</i>"
        )
    if len(state["queue"]) > 10:
        lines.append(f"…and {len(state['queue']) - 10} more track(s)")
    await reply(update, "\n".join(lines))


async def volume(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "Usage: <code>/volume 1-200</code>")
        return
    level = int(context.args[0])
    if not 1 <= level <= 200:
        await reply(update, "❌ Volume must be between 1 and 200.")
        return
    chat_id = update.effective_chat.id
    try:
        await runtime.get_calls().change_volume_call(chat_id, level)
        runtime._sessions.setdefault(
            chat_id,
            {"current": None, "queue": [], "volume": 100},
        )["volume"] = level
        await reply(update, f"🔊 Volume set to <b>{level}%</b>.")
    except Exception:
        await reply(update, "❌ Couldn't change volume. Start a track first.")


def register_handlers() -> None:
    application.add_handler(CommandHandler("play", play, block=False))
    application.add_handler(CommandHandler("pause", pause, block=False))
    application.add_handler(CommandHandler("resume", resume, block=False))
    application.add_handler(CommandHandler("skip", skip, block=False))
    application.add_handler(CommandHandler("end", end, block=False))
    application.add_handler(CommandHandler("queue", queue, block=False))
    application.add_handler(CommandHandler("volume", volume, block=False))
