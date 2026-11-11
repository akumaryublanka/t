"""Telegram commands and interactive controls for MikasaVerse music."""

import asyncio
import random
import shutil
from datetime import datetime, timezone
from html import escape

from telegram import Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, db
from waifu.music import runtime
from waifu.music.downloader import download_audio_sync, extract_info_sync, extract_playlist_sync
from waifu.music.formatting import duration, group_only, player_keyboard, reply


music_favorites = db["music_favorites"]


def _new_state() -> dict:
    return {"current": None, "queue": [], "volume": 100, "loop_remaining": 0, "paused": False}


def _playing_text(track: dict, queued: int = 0) -> str:
    text = (
        "🎶 <b>MIKASAVERSE · NOW PLAYING</b>\n"
        f"🎧 {escape(track['title'])}\n"
        f"⏱️ {duration(track.get('duration'))}\n"
        f"👤 Requested by {escape(track.get('requested_by', 'Unknown'))}"
    )
    if queued:
        text += f"\n\n➕ Added <b>{queued}</b> more track(s) to the queue."
    return text


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
                f"➕ <b>Added to queue</b>\n{track_text}\n"
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
        runtime.LOGGER.warning("Music request could not be completed: %s", exc)
        error_text = str(exc).lower()
        error_name = type(exc).__name__.lower()
        if "noactivegroupcall" in error_name or "no active group call" in error_text:
            await reply(
                update,
                "🎙️ <b>Voice Chat abhi OFF hai!</b>\n"
                "Pehle group mein Voice Chat ON karo, phir <code>/play</code> dobara bhejo. "
                "Assistant khud join karke music play karega.",
            )
        else:
            await reply(
                update,
                "🌸 <b>Oops! Ye track abhi play nahi ho pa raha.</b>\n"
                "Koi doosra song try karo — MikasaVerse music ke liye ready hai! 🎶",
            )


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
    if await runtime.skip_current(chat_id):
        await reply(update, "⏭️ Skipped the current track.")
    else:
        await reply(update, "ℹ️ Track already changed or nothing is playing.")


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
    await reply(update, "\n".join(lines), reply_markup=player_keyboard(update.effective_chat.id, paused=state.get("paused", False)))


async def current(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = await runtime.session_snapshot(update.effective_chat.id)
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


async def remove_track(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = runtime._sessions.get(update.effective_chat.id)
    if not state or not state.get("queue"):
        await reply(update, "ℹ️ There are no upcoming tracks to remove.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await reply(update, "Usage: <code>/remove 2</code> · Use /queue to see track positions.")
        return
    position = int(context.args[0])
    if not 1 <= position <= len(state["queue"]):
        await reply(update, f"❌ Choose a queue position between 1 and {len(state['queue'])}.")
        return
    track = state["queue"].pop(position - 1)
    await reply(update, f"🗑️ Removed from queue: <b>{escape(track['title'])}</b>")


async def move_track(update: Update, context: CallbackContext) -> None:
    if not await _require_music(update):
        return
    state = runtime._sessions.get(update.effective_chat.id)
    tracks = state.get("queue", []) if state else []
    if len(tracks) < 2:
        await reply(update, "ℹ️ Add at least two upcoming tracks before moving a song.")
        return
    if len(context.args) != 2 or not all(value.isdigit() for value in context.args):
        await reply(update, "Usage: <code>/move 3 1</code> · Moves queue item 3 to position 1.")
        return
    source, destination = map(int, context.args)
    if not 1 <= source <= len(tracks) or not 1 <= destination <= len(tracks):
        await reply(update, f"❌ Positions must be between 1 and {len(tracks)}.")
        return
    track = tracks.pop(source - 1)
    tracks.insert(destination - 1, track)
    await reply(update, f"↕️ Moved <b>{escape(track['title'])}</b> to queue position <b>{destination}</b>.")


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
        if await runtime.change_volume(chat_id, level):
            await reply(update, f"🔊 Volume set to <b>{level}%</b>.")
        else:
            await reply(update, "❌ Couldn't change volume. Start a track first.")
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
            await query.message.edit_reply_markup(reply_markup=player_keyboard(chat_id, paused=True))
            await query.answer("Paused")
        elif action == "resume":
            if not await runtime.resume_chat(chat_id):
                await query.answer("No paused track.", show_alert=True)
                return
            await query.message.edit_reply_markup(reply_markup=player_keyboard(chat_id, paused=False))
            await query.answer("Resumed")
        elif action == "skip":
            await query.answer("Skipping…")
            await runtime.skip_current(chat_id)
        elif action == "replay":
            await runtime.replay_current(chat_id)
            await query.answer("Replaying current track")
        elif action == "loop":
            state["loop_remaining"] = 0 if state.get("loop_remaining", 0) else 1
            await query.answer("Loop disabled" if state["loop_remaining"] == 0 else "One repeat enabled")
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
    state = runtime._sessions.get(update.effective_chat.id)
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
            pattern=r"^mv:(pause|resume|skip|replay|end|queue|loop|fav):(-?\d+)$",
            block=False,
        )
    )
