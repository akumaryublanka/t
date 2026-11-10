"""Telegram commands and interactive controls for MikasaVerse music."""

import asyncio
import random
from html import escape

from telegram import Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application
from waifu.music import runtime
from waifu.music.downloader import extract_info_sync, extract_playlist_sync
from waifu.music.formatting import duration, group_only, player_keyboard, reply


def _new_state() -> dict:
    return {"current": None, "queue": [], "volume": 100, "loop_remaining": 0, "paused": False}


def _playing_text(track: dict, queued: int = 0) -> str:
    text = (
        "🎶 <b>MIKASAVERSE · NOW PLAYING</b>\\n"
        f"🎧 {escape(track['title'])}\\n"
        f"⏱️ {duration(track.get('duration'))}\\n"
        f"👤 Requested by {escape(track.get('requested_by', 'Unknown'))}"
    )
    if queued:
        text += f"\\n\\n➕ Added <b>{queued}</b> more track(s) to the queue."
    return text


async def _require_music(update: Update) -> bool:
    if not group_only(update):
        await reply(update, "🎵 Music commands work inside a group voice chat.")
        return False
    if runtime.get_calls() is None:
        await reply(update, runtime.unavailable_message())
        return False
    return True


async def _add_tracks(update: Update, tracks: list[dict]) -> None:
    chat_id = update.effective_chat.id
    requester = update.effective_user.first_name if update.effective_user else "Unknown"
    for track in tracks:
        track["requested_by"] = requester

    async with runtime._chat_locks[chat_id]:
        state = runtime._sessions.setdefault(chat_id, _new_state())
        if state.get("current") is None:
            first, *remaining = tracks
            try:
                await runtime._start_track(chat_id, first)
            except Exception:
                runtime._sessions.pop(chat_id, None)
                raise
            state["queue"].extend(remaining)
            await reply(
                update,
                _playing_text(first, len(remaining)),
                reply_markup=player_keyboard(chat_id),
            )
        else:
            state["queue"].extend(tracks)
            position = len(state["queue"]) - len(tracks) + 1
            if len(tracks) == 1:
                track_text = f"🎵 {escape(tracks[0]['title'])}"
            else:
                track_text = f"🎵 Added <b>{len(tracks)}</b> playlist tracks"
            await reply(
                update,
                f"➕ <b>Added to queue</b>\\n{track_text}\\n"
                f"📍 Starting position: <b>{position}</b>",
            )


async def play(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    query = " ".join(context.args).strip()
    if not query:
        await reply(update, "Usage: <code>/play song name, YouTube link, or playlist link</code>")
        return
    if len(query) > 500:
        await reply(update, "❌ Search text or URL is too long.")
        return

    await reply(update, "🔎 <i>Finding your track…</i>")
    try:
        tracks = []
        if query.startswith(("http://", "https://")) and "list=" in query:
            tracks = await asyncio.to_thread(extract_playlist_sync, query, 20)
        if not tracks:
            tracks = [await asyncio.to_thread(extract_info_sync, query)]
        await _add_tracks(update, tracks)
    except Exception as exc:
        runtime.LOGGER.warning("Music request failed: %s", exc)
        error_text = str(exc).lower()
        error_name = type(exc).__name__.lower()
        if "noactivegroupcall" in error_name or "no active group call" in error_text:
            await reply(
                update,
                "🎙️ <b>Voice Chat abhi OFF hai!</b>\\n"
                "Pehle group mein Voice Chat ON karo, phir <code>/play</code> dobara bhejo. "
                "Assistant khud join karke music play karega.",
            )
        elif any(marker in error_text for marker in ("sign in to confirm", "cookies", "youtube")):
            await reply(
                update,
                "⚠️ <b>YouTube ne anonymous playback block kar diya.</b>\\n"
                "Cookies ke bina doosra song title ya SoundCloud link try karo.",
            )
        else:
            await reply(
                update,
                "❌ <b>Music playback start nahi ho paya.</b>\\n"
                f"<code>{escape(str(exc)[:240])}</code>",
            )


async def pause(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    await runtime.get_calls().pause(update.effective_chat.id)
    state = runtime._sessions.get(update.effective_chat.id)
    if state:
        state["paused"] = True
    await reply(update, "⏸️ Music paused.")


async def resume(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    await runtime.get_calls().resume(update.effective_chat.id)
    state = runtime._sessions.get(update.effective_chat.id)
    if state:
        state["paused"] = False
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
    await runtime._advance_queue(chat_id, announce=True, force_skip=True)


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
    await reply(update, "⏹️ <b>Music stopped.</b> Queue cleared and assistant left the voice chat.")


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
        f"▶️ <b>Now:</b> {escape(current['title'])} "
        f"<i>({duration(current.get('duration'))})</i>",
    ]
    for index, track in enumerate(state["queue"][:15], start=1):
        lines.append(
            f"{index}. {escape(track['title'])} "
            f"<i>({duration(track.get('duration'))})</i>"
        )
    if len(state["queue"]) > 15:
        lines.append(f"…and {len(state['queue']) - 15} more track(s)")
    await reply(update, "\\n".join(lines), reply_markup=player_keyboard(update.effective_chat.id, paused=state.get("paused", False)))


async def current(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = runtime._sessions.get(update.effective_chat.id)
    if not state or not state.get("current"):
        await reply(update, "ℹ️ Nothing is playing right now.")
        return
    await reply(
        update,
        _playing_text(state["current"]),
        reply_markup=player_keyboard(update.effective_chat.id, paused=state.get("paused", False)),
    )


async def replay(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    try:
        if await runtime.replay_current(update.effective_chat.id):
            await reply(update, "🔁 Replaying the current track.")
        else:
            await reply(update, "ℹ️ Nothing is playing right now.")
    except Exception as exc:
        runtime.LOGGER.exception("Replay failed in %s", update.effective_chat.id)
        await reply(update, f"❌ Could not replay this track: <code>{escape(str(exc)[:180])}</code>")


async def loop(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = runtime._sessions.get(update.effective_chat.id)
    if not state or not state.get("current"):
        await reply(update, "ℹ️ Start a track before setting loop.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "Usage: <code>/loop 0-10</code> · 0 disables repeat; 1-10 repeats the current track.")
        return
    count = int(context.args[0])
    if not 0 <= count <= 10:
        await reply(update, "❌ Loop range is 0-10.")
        return
    state["loop_remaining"] = count
    await reply(update, f"🔁 Current track will repeat <b>{count}</b> more time(s)." if count else "↩️ Track loop disabled.")


async def shuffle(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = runtime._sessions.get(update.effective_chat.id)
    if not state or not state.get("queue"):
        await reply(update, "ℹ️ There are no upcoming tracks to shuffle.")
        return
    random.shuffle(state["queue"])
    await reply(update, f"🔀 Shuffled <b>{len(state['queue'])}</b> upcoming track(s).")


async def clear_queue(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = runtime._sessions.get(update.effective_chat.id)
    if not state or not state.get("queue"):
        await reply(update, "ℹ️ The upcoming queue is already empty.")
        return
    removed = len(state["queue"])
    state["queue"].clear()
    await reply(update, f"🧹 Cleared <b>{removed}</b> upcoming track(s). Current track will continue.")


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
        runtime._sessions.setdefault(chat_id, _new_state())["volume"] = level
        await reply(update, f"🔊 Volume set to <b>{level}%</b>.")
    except Exception:
        await reply(update, "❌ Couldn't change volume. Start a track first.")


async def music_controls(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    try:
        _, action, raw_chat_id = query.data.split(":")
        chat_id = int(raw_chat_id)
    except (ValueError, AttributeError):
        await query.answer("Invalid music control.", show_alert=True)
        return
    if query.message.chat.id != chat_id:
        await query.answer("This control belongs to another group.", show_alert=True)
        return

    state = runtime._sessions.get(chat_id)
    if action == "queue":
        if not state or not state.get("current"):
            await query.answer("The queue is empty.", show_alert=True)
            return
        lines = ["🎶 <b>MIKASAVERSE QUEUE</b>", "", f"▶️ {escape(state['current']['title'])}"]
        for index, track in enumerate(state["queue"][:15], start=1):
            lines.append(f"{index}. {escape(track['title'])}")
        await query.message.reply_text("\\n".join(lines), parse_mode="HTML")
        await query.answer()
        return
    if not state or not state.get("current") or runtime.get_calls() is None:
        await query.answer("Nothing is playing.", show_alert=True)
        return

    try:
        if action == "pause":
            await runtime.get_calls().pause(chat_id)
            state["paused"] = True
            await query.message.edit_reply_markup(reply_markup=player_keyboard(chat_id, paused=True))
            await query.answer("Paused")
        elif action == "resume":
            await runtime.get_calls().resume(chat_id)
            state["paused"] = False
            await query.message.edit_reply_markup(reply_markup=player_keyboard(chat_id, paused=False))
            await query.answer("Resumed")
        elif action == "skip":
            await query.answer("Skipping…")
            await runtime._advance_queue(chat_id, announce=True, force_skip=True)
        elif action == "replay":
            await runtime.replay_current(chat_id)
            await query.answer("Replaying current track")
        elif action == "loop":
            state["loop_remaining"] = 0 if state.get("loop_remaining", 0) else 1
            await query.answer("Loop disabled" if state["loop_remaining"] == 0 else "One repeat enabled")
        elif action == "end":
            async with runtime._chat_locks[chat_id]:
                runtime._sessions.pop(chat_id, None)
                await runtime.get_calls().leave_call(chat_id)
            await query.answer("Music stopped")
            await query.message.reply_text("⏹️ Music stopped and assistant left the voice chat.")
    except Exception as exc:
        runtime.LOGGER.exception("Music button action %s failed in %s", action, chat_id)
        await query.answer(f"Action failed: {str(exc)[:120]}", show_alert=True)


def register_handlers() -> None:
    application.add_handler(CommandHandler("play", play, block=False))
    application.add_handler(CommandHandler("pause", pause, block=False))
    application.add_handler(CommandHandler("resume", resume, block=False))
    application.add_handler(CommandHandler("skip", skip, block=False))
    application.add_handler(CommandHandler("end", end, block=False))
    application.add_handler(CommandHandler("queue", queue, block=False))
    application.add_handler(CommandHandler("current", current, block=False))
    application.add_handler(CommandHandler("playing", current, block=False))
    application.add_handler(CommandHandler("replay", replay, block=False))
    application.add_handler(CommandHandler("loop", loop, block=False))
    application.add_handler(CommandHandler("shuffle", shuffle, block=False))
    application.add_handler(CommandHandler("clearqueue", clear_queue, block=False))
    application.add_handler(CommandHandler("volume", volume, block=False))
    application.add_handler(
        CallbackQueryHandler(
            music_controls,
            pattern=r"^mv:(pause|resume|skip|replay|end|queue|loop):(-?\\d+)$",
            block=False,
        )
    )
