"""Telegram commands and interactive controls for MikasaVerse music."""

import asyncio
import random
import shutil
from datetime import datetime, timezone
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, db
from waifu.music import runtime
from waifu.music.downloader import (
    download_audio_sync,
    extract_info_sync,
    extract_playlist_sync,
    get_related_tracks_sync,
)
from waifu.music.formatting import duration, group_only, player_keyboard, reply
from waifu.music.thumbnail import create_music_thumbnail
from waifu.music.mix_thumbnail import create_mix_thumbnail


music_favorites = db["music_favorites"]
_MIX_CACHE: dict[int, dict] = {}


def _track_key(track: dict | None) -> str:
    if not track:
        return ""
    return str(track.get("source") or track.get("title") or "")


def _mix_keyboard(chat_id: int, tracks: list[dict]) -> InlineKeyboardMarkup:
    """Number strip and close button matching the MIKASA MIX poster reference."""
    numbers = [
        InlineKeyboardButton(str(index), callback_data=f"mv:mixpick:{chat_id}:{index}", style="primary")
        for index in range(1, min(len(tracks), 5) + 1)
    ]
    rows = [numbers] if numbers else []
    rows.append([
        InlineKeyboardButton("🗑 CLOSE", callback_data=f"mv:mixclose:{chat_id}", style="danger")
    ])
    return InlineKeyboardMarkup(rows)


def _new_state() -> dict:
    return {
        "current": None,
        "queue": [],
        "volume": 100,
        "loop_remaining": 0,
        "paused": False,
        "autoplay": False,
        "autoplay_seen": [],
    }


def _playing_text(track: dict, queued: int = 0) -> str:
    title = escape(str(track.get("title") or "Unknown Track"))
    requester = escape(str(track.get("requested_by") or "Unknown"))
    heading = "🎬 <b>VIDEO STARTED STREAMING</b>" if track.get("media_type") == "video" else "➜ <b>STARTED STREAMING</b>"
    text = (
        f"{heading} |\n\n"
        f"▸ <b>TITLE:</b> {title}\n"
        f"▸ <b>DURATION:</b> {duration(track.get('duration'))} MINUTES\n"
        f"▸ <b>REQUESTED BY:</b> {requester}"
    )
    if queued:
        text += f"\n\n➕ Added <b>{queued}</b> more track(s) to the queue."
    return text

async def _reply_now_playing(update: Update, track: dict, queued: int = 0, paused: bool = False) -> None:
    """Send the artwork card quickly; fall back to text if image work is slow."""
    message = update.effective_message
    if not message:
        return
    caption = _playing_text(track, queued)
    keyboard = player_keyboard(update.effective_chat.id, paused=paused)

    async def send_card() -> None:
        thumbnail = await asyncio.to_thread(
            create_music_thumbnail,
            title=track.get("title", "Unknown Track"),
            artist=track.get("artist") or track.get("uploader") or "Unknown Artist",
            artwork=track.get("thumbnail"),
            duration=track.get("duration"),
            requester=track.get("requested_by"),
        )
        await message.get_bot().send_photo(
            chat_id=message.chat_id,
            photo=thumbnail,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    try:
        await asyncio.wait_for(send_card(), timeout=4.0)
    except Exception:
        runtime.LOGGER.info(
            "Player artwork timed out or failed; sending the fast text player instead.",
            exc_info=True,
        )
        try:
            await reply(update, caption, reply_markup=keyboard)
        except Exception:
            # Playback is already active. A Telegram status-message failure
            # must never be reported as a failed music request.
            runtime.LOGGER.warning(
                "Could not send the now-playing message after playback started.",
                exc_info=True,
            )


async def _get_favorites(user_id: int) -> list[dict]:
    cursor = music_favorites.find({"user_id": user_id}).sort("created_at", -1).limit(25)
    return [item async for item in cursor]


async def _save_favorite(user_id: int, track: dict) -> bool:
    await music_favorites.create_index(
        [("user_id", 1), ("source", 1)],
        unique=True,
        name="music_favorites_user_source",
    )
    result = await music_favorites.update_one(
        {"user_id": user_id, "source": track["source"]},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "source": track["source"],
                "title": track.get("title", "Unknown track"),
                "duration": track.get("duration"),
                "created_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    return result.upserted_id is not None


async def _require_music(update: Update) -> bool:
    if not group_only(update):
        await reply(update, "🎵 Music commands work inside a group voice chat.")
        return False
    if runtime.get_calls() is None:
        await reply(update, runtime.unavailable_message())
        return False
    return True


async def _add_tracks(
    update: Update,
    tracks: list[dict],
    status_message=None,
) -> None:
    chat_id = update.effective_chat.id
    if not tracks:
        if status_message:
            await status_message.edit_text("🎵 No playable tracks were found.")
        return
    requester = update.effective_user.first_name if update.effective_user else "Unknown"
    for track in tracks:
        track["requested_by"] = requester

    start_state = None
    first = None
    queued_text = None
    async with runtime._chat_locks[chat_id]:
        state = runtime._sessions.setdefault(chat_id, _new_state())
        if state.get("current") is None and not state.get("transitioning"):
            first, *remaining = tracks
            state["transitioning"] = True
            # Reserve playlist items before downloading so later requests stay
            # behind them in the same order.
            state["queue"].extend(remaining)
            start_state = state
        else:
            state["queue"].extend(tracks)
            position = len(state["queue"]) - len(tracks) + 1
            if len(tracks) == 1:
                track_text = f"🎵 {escape(tracks[0]['title'])}"
            else:
                track_text = f"🎵 Added <b>{len(tracks)}</b> playlist tracks"
            queued_text = (
                f"➕ <b>Added to queue</b>\n{track_text}\n"
                f"📍 Starting position: <b>{position}</b>"
            )

    if first is None:
        if status_message:
            try:
                await status_message.edit_text(
                    queued_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                await reply(update, queued_text)
        else:
            await reply(update, queued_text)
        return

    if status_message:
        preparing = (
            "🎬 <b>Preparing video for the voice chat…</b>"
            if first.get("media_type") == "video"
            else "🎧 <b>Preparing high-quality audio…</b>"
        )
        try:
            await status_message.edit_text(preparing, parse_mode="HTML")
        except Exception:
            pass

    try:
        # Network lookup/download runs without the per-chat command lock.
        await runtime._start_track(chat_id, first)
    except Exception:
        async with runtime._chat_locks[chat_id]:
            if runtime._sessions.get(chat_id) is start_state:
                await runtime._stop_chat_locked(chat_id)
        raise

    await _reply_now_playing(update, first, len(start_state.get("queue", [])))
    if status_message:
        try:
            await status_message.delete()
        except Exception:
            pass


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

    status = await update.effective_message.reply_text(
        "🔎 <b>Searching YouTube Music…</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    try:
        tracks = []
        if query.startswith(("http://", "https://")) and "list=" in query:
            tracks = await asyncio.wait_for(
                asyncio.to_thread(extract_playlist_sync, query, 20),
                timeout=30,
            )
        if not tracks:
            track = await asyncio.wait_for(
                asyncio.to_thread(extract_info_sync, query),
                timeout=20,
            )
            tracks = [track]
        await status.edit_text(
            "🎧 <b>Track found.</b> Preparing playback…",
            parse_mode="HTML",
        )
        await _add_tracks(update, tracks, status_message=status)
    except asyncio.TimeoutError:
        runtime.LOGGER.warning("Music search timed out for query: %s", query[:120])
        try:
            await status.edit_text(
                "⏳ <b>Search is taking too long.</b> Try a shorter song title or a direct link.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as exc:
        runtime.LOGGER.warning("Music request could not be completed: %s", exc)
        error_text = str(exc).lower()
        error_name = type(exc).__name__.lower()
        if "noactivegroupcall" in error_name or "no active group call" in error_text:
            user_text = (
                "🎙️ <b>Voice Chat abhi OFF hai!</b>\n"
                "Pehle group mein Voice Chat ON karo, phir <code>/play</code> dobara bhejo. "
                "Assistant khud join karke music play karega."
            )
        elif "short preview" in error_text or "full song could not be verified" in error_text:
            user_text = (
                "🌸 <b>Sirf short preview mila, full song nahi.</b>\n"
                "Maine incomplete audio play nahi ki. Official full-song link try karo."
            )
        elif "timed out" in error_text or "timeout" in error_text or "drm" in error_text:
            user_text = (
                "🌸 <b>Full song source abhi respond nahi kar raha.</b>\n"
                "Thodi der baad retry karo, ya official full-song YouTube link bhejo."
            )
        else:
            user_text = (
                "🌸 <b>Ye track abhi play nahi ho pa raha.</b>\n"
                "Koi doosra version ya direct YouTube/SoundCloud link try karo."
            )
        try:
            await status.edit_text(
                user_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            await reply(update, user_text)


async def vplay(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    query = " ".join(context.args).strip()
    if not query:
        await reply(update, "Usage: <code>/vplay video title or YouTube link</code>")
        return
    if len(query) > 1000:
        await reply(update, "❌ Video title or link is too long.")
        return

    status = await update.effective_message.reply_text(
        "🎬 <b>Finding a video for voice chat…</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    track = {
        "source": query,
        "title": query if query.startswith(("http://", "https://")) else f"Video: {query}",
        "duration": None,
        "media_type": "video",
    }
    try:
        await status.edit_text(
            "⬇️ <b>Preparing video + audio stream…</b>",
            parse_mode="HTML",
        )
        await _add_tracks(update, [track], status_message=status)
    except Exception as exc:
        runtime.LOGGER.warning("VC video request could not be completed: %s", exc)
        message = (
            "🎬 <b>Video couldn't be started.</b>\n"
            "Try a shorter public YouTube video or another direct video link. "
            "Long or unavailable videos may not be supported."
        )
        try:
            await status.edit_text(message, parse_mode="HTML")
        except Exception:
            await reply(update, message)


async def pause(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    if await runtime.pause_chat(update.effective_chat.id):
        await reply(update, "⏸️ Music paused.")
    else:
        await reply(update, "ℹ️ No active track is playing.")


async def resume(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    if await runtime.resume_chat(update.effective_chat.id):
        await reply(update, "▶️ Music resumed.")
    else:
        await reply(update, "ℹ️ No paused track is available.")


async def skip(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    state = runtime._sessions.get(chat_id)
    if not state or not state.get("current"):
        await reply(update, "ℹ️ Nothing is playing right now.")
        return
    await reply(update, "⏭️ <b>Skipping current track…</b>")

    async def finish_skip() -> None:
        try:
            await runtime.skip_current(chat_id)
        except Exception:
            runtime.LOGGER.exception("Background skip failed in chat %s", chat_id)

    asyncio.create_task(finish_skip())


async def end(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    chat_id = update.effective_chat.id
    await runtime.stop_chat(chat_id)
    await reply(update, "⏹️ <b>Music stopped.</b> Queue cleared and assistant left the voice chat.")


async def queue(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = await runtime.session_snapshot(update.effective_chat.id)
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
    await reply(update, "\n".join(lines), reply_markup=player_keyboard(
            update.effective_chat.id,
            paused=state.get("paused", False),
            autoplay=state.get("autoplay", False),
        ))


async def current(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = await runtime.session_snapshot(update.effective_chat.id)
    if not state or not state.get("current"):
        await reply(update, "ℹ️ Nothing is playing right now.")
        return
    await _reply_now_playing(
        update,
        state["current"],
        paused=state.get("paused", False),
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
    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "Usage: <code>/loop 0-10</code> · 0 disables repeat; 1-10 repeats the current track.")
        return
    count = int(context.args[0])
    if not 0 <= count <= 10:
        await reply(update, "❌ Loop range is 0-10.")
        return
    if not await runtime.set_loop_remaining(update.effective_chat.id, count):
        await reply(update, "ℹ️ Start a track before setting loop.")
        return
    await reply(update, f"🔁 Current track will repeat <b>{count}</b> more time(s)." if count else "↩️ Track loop disabled.")


async def shuffle(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    count = await runtime.shuffle_queue(update.effective_chat.id)
    if not count:
        await reply(update, "ℹ️ There are no upcoming tracks to shuffle.")
        return
    await reply(update, f"🔀 Shuffled <b>{count}</b> upcoming track(s).")


async def clear_queue(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    removed = await runtime.clear_queue(update.effective_chat.id)
    if not removed:
        await reply(update, "ℹ️ The upcoming queue is already empty.")
        return
    await reply(update, f"🧹 Cleared <b>{removed}</b> upcoming track(s). Current track will continue.")


async def remove_track(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "Usage: <code>/remove 2</code> · Use /queue to see track positions.")
        return
    position = int(context.args[0])
    track = await runtime.remove_queued_track(update.effective_chat.id, position)
    if not track:
        await reply(update, "❌ That queue position is not available.")
        return
    await reply(update, f"🗑️ Removed from queue: <b>{escape(track['title'])}</b>")


async def move_track(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    if len(context.args) != 2 or not all(value.isdigit() for value in context.args):
        await reply(update, "Usage: <code>/move 3 1</code> · Moves queue item 3 to position 1.")
        return
    source, destination = map(int, context.args)
    if not await runtime.move_queued_track(update.effective_chat.id, source, destination):
        await reply(update, "ℹ️ Queue positions are no longer valid. Check /queue and try again.")
        return
    await reply(update, f"↕️ Moved queue item <b>{source}</b> to position <b>{destination}</b>.")


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
        if await runtime.change_volume(chat_id, level):
            await reply(update, f"🔊 Volume set to <b>{level}%</b>.")
        else:
            await reply(update, "❌ Couldn't change volume. Start a track first.")
    except Exception:
        runtime.LOGGER.exception("Volume change failed in %s", chat_id)
        await reply(update, "❌ Couldn't change volume. Start a track first.")


async def music_controls(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    try:
        parts = str(query.data or "").split(":")
        if len(parts) not in (3, 4):
            raise ValueError("Invalid callback shape")
        _, action, raw_chat_id = parts[:3]
        extra = parts[3] if len(parts) == 4 else None
        chat_id = int(raw_chat_id)
    except (ValueError, AttributeError):
        await query.answer("Invalid music control.", show_alert=True)
        return
    if query.message.chat.id != chat_id:
        await query.answer("This control belongs to another group.", show_alert=True)
        return

    state = await runtime.session_snapshot(chat_id)

    if action in {"mixclose", "close"}:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer("Player closed" if action == "close" else "MIKASA MIX closed")
        return

    if action == "autoplay":
        if not state or not state.get("current"):
            await query.answer("Nothing is playing.", show_alert=True)
            return
        enabled = await runtime.toggle_autoplay(chat_id)
        if enabled is None:
            await query.answer("Nothing is playing.", show_alert=True)
            return
        await query.message.edit_reply_markup(
            reply_markup=player_keyboard(
                chat_id,
                paused=state.get("paused", False),
                autoplay=enabled,
            )
        )
        await query.answer(
            "Auto Mix enabled — similar songs will continue after the queue."
            if enabled else "Auto Mix disabled"
        )
        return

    if action == "mix":
        if not state or not state.get("current"):
            await query.answer("Play a track first.", show_alert=True)
            return
        seed = dict(state["current"])
        await query.answer("Finding your MIKASA MIX…")
        try:
            tracks = await asyncio.wait_for(
                asyncio.to_thread(get_related_tracks_sync, seed, 5),
                timeout=20,
            )
        except asyncio.TimeoutError:
            tracks = []
        except Exception:
            runtime.LOGGER.exception("MIKASA MIX lookup failed in %s", chat_id)
            tracks = []

        latest = await runtime.session_snapshot(chat_id)
        if not latest or _track_key(latest.get("current")) != _track_key(seed):
            await query.message.reply_text(
                "🎵 The current track changed. Open MIKASA MIX again for the new song."
            )
            return
        if not tracks:
            await query.message.reply_text(
                "🌸 <b>No MIKASA MIX tracks found right now.</b>\n"
                "Try refreshing in a moment or play another song.",
                parse_mode="HTML",
            )
            return

        _MIX_CACHE[chat_id] = {"seed": _track_key(seed), "tracks": tracks}
        if len(_MIX_CACHE) > 100:
            oldest_chat = next(iter(_MIX_CACHE))
            if oldest_chat != chat_id:
                _MIX_CACHE.pop(oldest_chat, None)

        try:
            poster = await asyncio.to_thread(
                create_mix_thumbnail,
                tracks=tracks[:5],
                current_track=seed,
                brand="MIKASAVERSE",
            )
            await query.message.reply_photo(
                photo=poster,
                caption=(
                    "🌸 <b>MIKASA MIX</b> · Inspired by "
                    f"<b>{escape(str(seed.get('title') or 'Now Playing'))}</b>\n"
                    "Choose a numbered song to add it to the queue."
                ),
                parse_mode="HTML",
                reply_markup=_mix_keyboard(chat_id, tracks),
            )
        except Exception:
            runtime.LOGGER.exception("MIKASA MIX poster render failed in %s", chat_id)
            lines = ["✨ <b>MIKASA MIX</b>", ""]
            for index, track in enumerate(tracks[:5], start=1):
                artist = track.get("artist") or track.get("uploader") or "Unknown artist"
                lines.append(
                    f"<b>{index}.</b> {escape(str(track.get('title') or 'Unknown track'))}\n"
                    f"   🎤 {escape(str(artist))} · {duration(track.get('duration'))}"
                )
            await query.message.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=_mix_keyboard(chat_id, tracks),
                disable_web_page_preview=True,
            )
        return

    if action in {"mixpick", "mixall"}:
        if not state or not state.get("current") or runtime.get_calls() is None:
            await query.answer("Music is not active in this group.", show_alert=True)
            return
        cached = _MIX_CACHE.get(chat_id)
        if not cached or cached.get("seed") != _track_key(state.get("current")):
            await query.answer("This mix has expired. Open MIKASA MIX again.", show_alert=True)
            return
        if action == "mixpick":
            if not extra or not extra.isdigit():
                await query.answer("Invalid song number.", show_alert=True)
                return
            index = int(extra)
            if not 1 <= index <= len(cached["tracks"]):
                await query.answer("That song is no longer available.", show_alert=True)
                return
            selected = [dict(cached["tracks"][index - 1])]
        else:
            selected = [dict(track) for track in cached["tracks"]]
        await query.answer("Adding to queue…")
        await _add_tracks(update, selected)
        return

    if action == "queue":
        if not state or not state.get("current"):
            await query.answer("The queue is empty.", show_alert=True)
            return
        lines = ["🎶 <b>MIKASAVERSE QUEUE</b>", "", f"▶️ {escape(state['current']['title'])}"]
        for index, track in enumerate(state["queue"][:15], start=1):
            lines.append(f"{index}. {escape(track['title'])}")
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")
        await query.answer()
        return
    if action == "fav":
        if not state or not state.get("current"):
            await query.answer("Nothing is playing.", show_alert=True)
            return
        try:
            added = await _save_favorite(query.from_user.id, state["current"])
            await query.answer("Added to favourites ❤️" if added else "Already in favourites ❤️", show_alert=True)
        except Exception:
            runtime.LOGGER.exception("Inline favorite save failed")
            await query.answer("Could not save favourite.", show_alert=True)
        return
    if not state or not state.get("current") or runtime.get_calls() is None:
        await query.answer("Nothing is playing.", show_alert=True)
        return

    try:
        if action == "pause":
            if not await runtime.pause_chat(chat_id):
                await query.answer("Nothing is playing.", show_alert=True)
                return
            await query.message.edit_reply_markup(reply_markup=player_keyboard(chat_id, paused=True, autoplay=state.get("autoplay", False)))
            await query.answer("Paused")
        elif action == "resume":
            if not await runtime.resume_chat(chat_id):
                await query.answer("No paused track.", show_alert=True)
                return
            await query.message.edit_reply_markup(reply_markup=player_keyboard(chat_id, paused=False, autoplay=state.get("autoplay", False)))
            await query.answer("Resumed")
        elif action == "skip":
            await query.answer("Skipping…")
            async def finish_button_skip() -> None:
                try:
                    await runtime.skip_current(chat_id)
                except Exception:
                    runtime.LOGGER.exception("Background button skip failed in chat %s", chat_id)
            asyncio.create_task(finish_button_skip())
        elif action == "replay":
            await runtime.replay_current(chat_id)
            await query.answer("Replaying current track")
        elif action == "loop":
            loop_count = await runtime.toggle_loop(chat_id)
            if loop_count is None:
                await query.answer("Nothing is playing.", show_alert=True)
            else:
                await query.answer("Loop disabled" if loop_count == 0 else "One repeat enabled")
        elif action == "end":
            await runtime.stop_chat(chat_id)
            await query.answer("Music stopped")
            await query.message.reply_text("⏹️ Music stopped and assistant left the voice chat.")
    except Exception as exc:
        runtime.LOGGER.exception("Music button action %s failed in %s", action, chat_id)
        await query.answer(f"Action failed: {str(exc)[:120]}", show_alert=True)


async def song_download(update: Update, context: CallbackContext) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await reply(update, "Usage: <code>/song song name or link</code>")
        return
    status = await update.effective_message.reply_text(
        "⬇️ <b>Preparing your audio download…</b>", parse_mode="HTML"
    )
    downloaded = None
    try:
        downloaded = await asyncio.to_thread(download_audio_sync, query)
        caption = (
            f"🎵 <b>{escape(downloaded['title'])}</b>\n"
            f"⏱️ {duration(downloaded.get('duration'))}\n"
            "🌸 <i>Downloaded by MikasaVerse</i>"
        )
        with open(downloaded["path"], "rb") as audio:
            await update.effective_message.reply_audio(
                audio=audio,
                title=downloaded["title"][:64],
                performer=(downloaded.get("uploader") or "Unknown")[:64],
                caption=caption,
                parse_mode="HTML",
            )
        await status.delete()
    except Exception as exc:
        runtime.LOGGER.warning("Song download failed: %s", exc)
        await status.edit_text(
            "❌ <b>Download failed.</b>\n"
            f"<code>{escape(str(exc)[:260])}</code>",
            parse_mode="HTML",
        )
    finally:
        if downloaded:
            await asyncio.to_thread(shutil.rmtree, downloaded["temp_dir"], True)


async def favorite(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    user = update.effective_user
    state = await runtime.session_snapshot(update.effective_chat.id)
    track = state.get("current") if state else None
    if not user or not track:
        await reply(update, "ℹ️ There is no current track to save.")
        return
    try:
        added = await _save_favorite(user.id, track)
        await reply(
            update,
            "❤️ Added the current track to your favourites."
            if added else "❤️ This track is already in your favourites.",
        )
    except Exception:
        runtime.LOGGER.exception("Could not save music favorite for %s", user.id)
        await reply(update, "❌ Couldn't save this favourite right now.")


async def favs(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not user:
        await reply(update, "Open favourites from your Telegram account.")
        return
    tracks = await _get_favorites(user.id)
    if not tracks:
        await reply(update, "❤️ You haven't saved any music favourites yet. Play a track and use /favorite.")
        return
    lines = ["❤️ <b>YOUR MUSIC FAVOURITES</b>", ""]
    for index, track in enumerate(tracks[:20], start=1):
        lines.append(
            f"{index}. {escape(track.get('title', 'Unknown track'))} "
            f"<i>({duration(track.get('duration'))})</i>"
        )
    lines += [
        "",
        "▶️ <code>/playfav number</code> to play one",
        "🗑️ <code>/delfav number</code> to remove one",
    ]
    if len(tracks) > 20:
        lines.append("Showing your latest 20 saved tracks.")
    await reply(update, "\n".join(lines))


async def play_favorite(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "Usage: <code>/playfav number</code> · see /favs")
        return
    index = int(context.args[0])
    tracks = await _get_favorites(update.effective_user.id)
    if not 1 <= index <= len(tracks):
        await reply(update, "❌ Favourite number not found. Check /favs.")
        return
    track = tracks[index - 1]
    await _add_tracks(
        update,
        [{
            "source": track["source"],
            "title": track.get("title") or "Unknown track",
            "duration": track.get("duration"),
        }],
    )


async def delete_favorite(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not user:
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "Usage: <code>/delfav number</code> · see /favs")
        return
    index = int(context.args[0])
    tracks = await _get_favorites(user.id)
    if not 1 <= index <= len(tracks):
        await reply(update, "❌ Favourite number not found. Check /favs.")
        return
    result = await music_favorites.delete_one(
        {"_id": tracks[index - 1]["_id"], "user_id": user.id}
    )
    await reply(
        update,
        "🗑️ Favourite removed." if result.deleted_count else "❌ Favourite was not found.",
    )



def register_handlers() -> None:
    # Let the dispatcher await commands; per-chat runtime locks serialize player operations.
    application.add_handler(CommandHandler("play", play))
    application.add_handler(CommandHandler("vplay", vplay))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("skip", skip))
    application.add_handler(CommandHandler("end", end))
    application.add_handler(CommandHandler("queue", queue))
    application.add_handler(CommandHandler("current", current))
    application.add_handler(CommandHandler("playing", current))
    application.add_handler(CommandHandler("replay", replay))
    application.add_handler(CommandHandler("loop", loop))
    application.add_handler(CommandHandler("shuffle", shuffle))
    application.add_handler(CommandHandler("clearqueue", clear_queue))
    application.add_handler(CommandHandler("remove", remove_track))
    application.add_handler(CommandHandler("move", move_track))
    application.add_handler(CommandHandler("volume", volume))
    application.add_handler(CommandHandler("song", song_download))
    application.add_handler(CommandHandler("favorite", favorite))
    application.add_handler(CommandHandler("favs", favs))
    application.add_handler(CommandHandler("favorites", favs))
    application.add_handler(CommandHandler("playfav", play_favorite))
    application.add_handler(CommandHandler("delfav", delete_favorite))
    application.add_handler(
        CallbackQueryHandler(
            music_controls,
            pattern=r"^mv:(pause|resume|skip|replay|end|queue|loop|fav|mix|mixpick|mixall|mixclose|autoplay):(-?\d+)(?::(\d+))?$",
        )
    )
