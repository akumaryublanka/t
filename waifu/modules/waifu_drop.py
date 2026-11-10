"""
modules/waifu_drop.py

Core game loop:
  - Rarity based drop chance
  - Special characters system (owner set kare)
  - Human group activity tracking
  - APScheduler timed drop every N minutes in recently active groups
  - No message-threshold drops; all drops follow the configured timer
  - /guess to claim
  - /fav to favourite
  - Delete the drop message immediately on claim/expiry; unclaimed drops expire after 5 minutes
  - Wrong guess reply auto-delete after 3 seconds
  - Royal style capture message with time taken
"""
import asyncio
import random
import time
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import (
    application, collection, group_user_totals_collection,
    top_global_groups_collection, user_collection,
    LOGGER, OWNER_ID, db,
)
from waifu.config import Config
from waifu.image_watermark import watermarked_photo
from waifu.modules.auto_delete import (
    schedule_delete, delete_now, WAIFU_DROP_DELAY, DROP_EXPIRY_DELAY,
    DROP_NOTICE_DELAY, WRONG_GUESS_DELAY, GUESS_COMMAND_DELAY,
)

# ── Per-chat in-memory state ──────────────────────────────────────────────────
_active_char:      dict[int, dict]  = {}
_active_msg:       dict[int, int]   = {}
_active_time:      dict[int, float] = {}
_claimed:          dict[int, int]   = {}
_last_user:        dict[int, dict]  = {}
_warned:           dict[int, float] = {}
_sent_ids:         dict[int, list]  = {}
_registered_chats: set[int]         = set()
_last_activity:    dict[int, float] = {}
_last_drop_sent:   dict[int, float] = {}  # Last successful timed drop per group.
_DROP_ACTIVE_WINDOW = max(30 * 60, Config.DROP_INTERVAL_MIN * 2 * 60)  # Require recent human activity; keep at least two scheduler intervals.

scheduler = AsyncIOScheduler(timezone="UTC")

_XP_PER_GUESS     = 50


# Special characters collection
special_collection = db["special_characters"]

# One canonical weight table is shared by normal drops, HClaim, and gacha.
# Weights are per character; lower weight means a rarer individual drop.
RARITY_WEIGHTS = {
    Config.RARITY_MAP[number]: weight
    for number, weight in Config.RARITY_WEIGHT_MAP.items()
    if number in Config.RARITY_MAP
}

RARITY_NUMERIC_MAP = Config.RARITY_MAP
_LEGACY_RARITY_NUMBERS = {26: 11, 27: 12, 28: 13, 29: 14, 30: 15, 31: 16}

def _rarity_label(value) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        number = value if value in RARITY_NUMERIC_MAP else _LEGACY_RARITY_NUMBERS.get(value)
        return RARITY_NUMERIC_MAP.get(number, Config.RARITY_MAP[1])
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            number = int(value)
            number = number if number in RARITY_NUMERIC_MAP else _LEGACY_RARITY_NUMBERS.get(number)
            return RARITY_NUMERIC_MAP.get(number, Config.RARITY_MAP[1])
        value = Config.RARITY_LEGACY_MAP.get(value, value)
        return value if value in RARITY_WEIGHTS else Config.RARITY_MAP[1]
    return Config.RARITY_MAP[1]

# Special characters are further reduced by 99.9% without overriding
# their rarity tier. This keeps Extreme rarer than every other tier.
_SPECIAL_WEIGHT_MULTIPLIER = 0.001

# Rarity display map
RARITY_DISPLAY = {rarity: rarity for rarity in Config.RARITY_MAP.values()}


def _drop_caption(char: dict) -> str:
    rarity = _rarity_label(char.get("rarity", Config.RARITY_MAP[1]))
    rarity_label = RARITY_DISPLAY.get(rarity, rarity)
    is_special = char.get("is_special", False)
    special_tag = "🌟 <b>SPECIAL DROP!</b>\n" if is_special else ""
    return (
        f"{special_tag}"
        f"✨ A <b>{rarity_label}</b> Character Appears! ✨\n"
        f"🔍 Use /guess to claim this mysterious character!\n"
        f"🪄 Hurry, before someone else snatches them!"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _maintenance_enabled() -> bool:
    """Read the shared maintenance flag before creating a drop."""
    doc = await db["bot_settings"].find_one({"_id": "maintenance"})
    if doc and doc.get("enabled", False):
        return True
    # Backward-compatible fallback for deployments that saved settings before
    # the legacy bot_settings switch was synchronized.
    panel = await db["miniapp_settings"].find_one({"_id": "general"})
    return bool(((panel or {}).get("data") or {}).get("maintenance_mode", False))


async def _owner_drop_settings() -> tuple[dict, dict]:
    game_doc = await db["miniapp_settings"].find_one({"_id": "game"})
    group_doc = await db["miniapp_settings"].find_one({"_id": "groups"})
    game = (game_doc or {}).get("data") or {}
    groups = (group_doc or {}).get("data") or {}
    return game, groups


def _configured_chat_ids(value) -> set[int]:
    ids = set()
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError:
            continue
    return ids


def _configured_chat_cooldowns(value) -> dict[int, int]:
    """Parse comma-separated group_id:seconds overrides from Owner Settings."""
    cooldowns = {}
    for item in str(value or "").split(","):
        if ":" not in item:
            continue
        raw_id, raw_seconds = item.split(":", 1)
        try:
            chat_id = int(raw_id.strip())
            seconds = max(0, min(86400, int(raw_seconds.strip())))
        except ValueError:
            continue
        cooldowns[chat_id] = seconds
    return cooldowns


def _rolling_window_size(total_chars: int) -> int:
    return max(20, total_chars // 2)


async def _safe_send(bot, chat_id: int, text: str, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        LOGGER.warning("Failed to send message in chat %s: %s", chat_id, e)
        return None


def _rarity_keys(value: str) -> set[str]:
    normalized = str(value or "").strip().casefold()
    return {key for key in (normalized, re.sub(r"^[^\w]+", "", normalized).strip()) if key}


async def _active_event_details() -> dict[str, dict]:
    """Return enabled events whose configured date window is active."""
    event_doc = await db["miniapp_settings"].find_one({"_id": "events"})
    general_doc = await db["miniapp_settings"].find_one({"_id": "general"})
    feature_doc = await db["miniapp_settings"].find_one({"_id": "features"})
    features = (feature_doc or {}).get("data") or {}
    if features.get("events", True) is False:
        return {}
    rows = (event_doc or {}).get("data", [])
    general = (general_doc or {}).get("data") or {}
    try:
        tz = ZoneInfo(str(general.get("timezone", "Asia/Kolkata")))
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    active = {}

    def parse_date(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).strip())
            return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
        except (TypeError, ValueError):
            return None

    for event in rows if isinstance(rows, list) else []:
        if not isinstance(event, dict) or event.get("enabled") is not True:
            continue
        start_at = parse_date(event.get("start_at"))
        end_at = parse_date(event.get("end_at"))
        if start_at and now < start_at:
            continue
        if end_at and now > end_at:
            continue
        try:
            reward = max(0, min(100000000, int(event.get("reward", 0) or 0)))
        except (TypeError, ValueError):
            reward = 0
        raw_rarity = str(event.get("rarity", "")).strip()
        details = {
            "name": str(event.get("name", "Event"))[:80],
            "rarity": Config.RARITY_LEGACY_MAP.get(raw_rarity, raw_rarity),
            "reward": reward,
        }
        for key in _rarity_keys(details["rarity"] or details["name"]):
            active[key] = details
    return active


async def _pick_character(all_chars: list, sent: list) -> dict:
    """Pick a character using saved Owner Panel rarity weights when configured."""

    special_docs = await special_collection.find({}).to_list(length=500)
    special_ids = {s["char_id"] for s in special_docs}
    unsent = [c for c in all_chars if c.get("id") not in sent] or all_chars

    custom_weights = {}
    rarity_doc = await db["miniapp_settings"].find_one({"_id": "rarities"})
    rarity_rows = (rarity_doc or {}).get("data", [])
    active_events = await _active_event_details()
    event_rarity_keys = set()
    for number, label in Config.RARITY_MAP.items():
        if number >= 11:
            event_rarity_keys.update(_rarity_keys(label))
    if isinstance(rarity_rows, list):
        for tier in rarity_rows:
            if isinstance(tier, dict) and tier.get("kind") == "event":
                event_rarity_keys.update(_rarity_keys(f'{tier.get("emoji", "")} {tier.get("name", "")}'))
                event_rarity_keys.update(_rarity_keys(tier.get("name", "")))
    canonical_names = {
        label.split(" ", 1)[1].casefold()
        for label in Config.RARITY_MAP.values()
    }
    stored_names = {
        str(tier.get("name", "")).strip().casefold()
        for tier in rarity_rows
        if isinstance(tier, dict)
    } if isinstance(rarity_rows, list) else set()
    # Ignore pre-MikasaVerse Owner Panel presets instead of letting their
    # obsolete rarity weights override the new canonical drop rates.
    if isinstance(rarity_rows, list) and canonical_names.issubset(stored_names):
        for tier in rarity_rows:
            if not isinstance(tier, dict):
                continue
            label = f'{str(tier.get("emoji", "")).strip()} {str(tier.get("name", "")).strip()}'.strip().casefold()
            name = str(tier.get("name", "")).strip().casefold()
            try:
                weight = max(0, float(tier.get("weight", 0))) if tier.get("enabled", True) else 0
            except (TypeError, ValueError):
                weight = 0
            if label:
                custom_weights[label] = weight
            if name:
                custom_weights[name] = weight

    weighted_pool = []
    for char in unsent:
        raw_rarity = char.get("rarity", Config.RARITY_MAP[1])
        rarity = _rarity_label(raw_rarity)
        if char.get("rarity") != rarity:
            char = {**char, "rarity": rarity}
        lookup_keys = set()
        lookup_keys.update(_rarity_keys(str(raw_rarity)))
        lookup_keys.update(_rarity_keys(rarity))
        lookup_keys.update(_rarity_keys(rarity.removeprefix("⚪ ")))
        custom = next((custom_weights[key] for key in lookup_keys if key in custom_weights), None)
        weight = custom if custom is not None else RARITY_WEIGHTS.get(rarity, RARITY_WEIGHTS.get(Config.RARITY_MAP[1], 1))

        # Event rarities only drop within an enabled event's date window.
        if lookup_keys.intersection(event_rarity_keys):
            event_match = next((active_events[key] for key in lookup_keys if key in active_events), None)
            if event_match is None:
                weight = 0

        # Zero-weight tiers stay disabled, including specially flagged characters.
        if char.get("id") in special_ids:
            if weight > 0:
                # Keep special characters rare while preserving the underlying
                # rarity order (especially the ultra-rare Extreme tier).
                weight *= _SPECIAL_WEIGHT_MULTIPLIER
                char = {**char, "is_special": True}
        if weight > 0:
            weighted_pool.append((char, weight))

    # Never bypass a disabled rarity or inactive event with an unweighted fallback.
    if not weighted_pool:
        LOGGER.info("No eligible characters for the active rarity/event settings; skipping drop")
        return {}
    chars = [c for c, _ in weighted_pool]
    weights = [w for _, w in weighted_pool]
    return random.choices(chars, weights=weights, k=1)[0]


async def _expire_drop(
    bot, chat_id: int, char_id: str, drop_msg_id: int,
    expiry_delay: int | None = None, claim_timeout: int | None = None,
) -> None:
    expiry = max(1, int(DROP_EXPIRY_DELAY if expiry_delay is None else expiry_delay))
    claim_window = max(1, min(expiry, int(expiry if claim_timeout is None else claim_timeout)))
    await asyncio.sleep(claim_window)

    current = _active_char.get(chat_id)
    if current and current.get("id") == char_id and _active_msg.get(chat_id) == drop_msg_id:
        # The claim window closes before the image is automatically deleted.
        _active_char.pop(chat_id, None)
        _active_msg.pop(chat_id, None)
        _claimed.pop(chat_id, None)
        _active_time.pop(chat_id, None)
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=drop_msg_id,
                caption="⏰ <b>The character got away!</b>\n<i>The claim window has expired.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        try:
            expire_msg = await bot.send_message(
                chat_id=chat_id,
                text="⏰ <b>The character got away!</b>\n<i>No one claimed them in time...</i>",
                parse_mode=ParseMode.HTML,
            )
            schedule_delete(bot, chat_id, expire_msg.message_id, DROP_NOTICE_DELAY)
        except Exception:
            pass

    remaining = max(0, expiry - claim_window)
    if remaining:
        await asyncio.sleep(remaining)
    # Always delete this specific drop, even if a newer drop replaced it.
    await delete_now(bot, chat_id, drop_msg_id, ignore_errors=True)

async def _send_drop(chat_id: int, bot) -> None:
    # Safety gate: a stale group must never receive a fresh drop even if a
    # future caller invokes this function outside the timed scheduler.
    last_activity = _last_activity.get(chat_id, 0)
    if not last_activity or time.time() - last_activity > _DROP_ACTIVE_WINDOW:
        LOGGER.debug("Group %s is inactive — skipping drop", chat_id)
        return

    # Never replace a live drop; each group has its own claim/expiry lifecycle.
    if chat_id in _active_char and chat_id in _active_msg:
        LOGGER.debug("Active drop already exists in chat %s — skipping duplicate", chat_id)
        return

    # Maintenance must stop timed drops.
    if await _maintenance_enabled():
        LOGGER.info("Maintenance enabled — skipping drop for chat %s", chat_id)
        return

    game_settings, group_settings = await _owner_drop_settings()
    if game_settings.get("drops_enabled", True) is False or group_settings.get("drops_enabled", True) is False:
        return
    allowed_groups = _configured_chat_ids(group_settings.get("allowed_group_ids", ""))
    blocked_groups = _configured_chat_ids(group_settings.get("blocked_group_ids", ""))
    if allowed_groups and chat_id not in allowed_groups:
        return
    if chat_id in blocked_groups:
        return
    try:
        cooldown = max(0, min(86400, int(game_settings.get("drop_cooldown_seconds", 0) or 0)))
    except (TypeError, ValueError):
        cooldown = 0
    try:
        group_default = max(0, min(86400, int(group_settings.get("default_drop_cooldown_seconds", 0) or 0)))
    except (TypeError, ValueError):
        group_default = 0
    group_overrides = _configured_chat_cooldowns(group_settings.get("group_drop_cooldowns", ""))
    cooldown = group_overrides.get(chat_id, group_default or cooldown)
    if cooldown and time.time() - _last_drop_sent.get(chat_id, 0) < cooldown:
        return

    all_chars = await collection.find({}).to_list(length=5000)
    if not all_chars:
        LOGGER.debug("No characters in DB — skipping drop for chat %s", chat_id)
        return

    window = _rolling_window_size(len(all_chars))
    sent   = _sent_ids.get(chat_id, [])

    # Pick character with rarity weights
    char = await _pick_character(all_chars, sent)
    if not char:
        return

    new_sent = sent + [char["id"]]
    _sent_ids[chat_id] = new_sent[-window:]

    _active_char[chat_id] = char
    _active_time[chat_id] = time.time()
    _claimed.pop(chat_id, None)

    try:
        marked_photo = await watermarked_photo(bot, char["img_url"])
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=marked_photo,
            caption=_drop_caption(char),
            parse_mode=ParseMode.HTML,
        )
        _active_msg[chat_id] = msg.message_id
        LOGGER.info("Drop sent to chat %s: %s (%s)%s",
                    chat_id, char["name"], _rarity_label(char.get("rarity", Config.RARITY_MAP[1])),
                    " [SPECIAL]" if char.get("is_special") else "")
        _last_drop_sent[chat_id] = time.time()
        # Best-effort private alerts for players who wishlisted this character.
        try:
            from waifu.modules.wishlist import notify_wishlist_match
            asyncio.create_task(notify_wishlist_match(bot, char))
        except Exception as notify_error:
            LOGGER.debug("Wishlist notification could not be queued: %s", notify_error)
        try:
            expiry_delay = max(1, min(86400, int(game_settings.get("drop_expiry_seconds", DROP_EXPIRY_DELAY))))
        except (TypeError, ValueError):
            expiry_delay = DROP_EXPIRY_DELAY
        try:
            claim_timeout = max(1, min(expiry_delay, int(game_settings.get("claim_timeout_seconds", expiry_delay))))
        except (TypeError, ValueError):
            claim_timeout = min(expiry_delay, DROP_EXPIRY_DELAY)
        asyncio.create_task(_expire_drop(bot, chat_id, char["id"], msg.message_id, expiry_delay, claim_timeout))

    except Exception as e:
        _active_char.pop(chat_id, None)
        _active_msg.pop(chat_id, None)
        _active_time.pop(chat_id, None)
        _claimed.pop(chat_id, None)
        LOGGER.warning("Drop failed in chat %s: %s", chat_id, e)


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def _timed_drop_job(bot) -> None:
    if await _maintenance_enabled():
        LOGGER.info("Maintenance enabled — timed drops skipped")
        return
    now = time.time()
    active_chats = [
        chat_id for chat_id in list(_registered_chats)
        if now - _last_activity.get(chat_id, 0) <= _DROP_ACTIVE_WINDOW
    ]
    if not active_chats:
        LOGGER.debug("Timed drop tick skipped — no recently active groups")
        return
    for chat_id in active_chats:
        await _send_drop(chat_id, bot)


def start_scheduler(bot) -> None:
    scheduler.add_job(
        _timed_drop_job,
        trigger=IntervalTrigger(minutes=Config.DROP_INTERVAL_MIN),
        kwargs={"bot": bot},
        id="timed_drop",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()
    LOGGER.info("Drop scheduler started — interval: every %d min", Config.DROP_INTERVAL_MIN)


# ── Message counter ───────────────────────────────────────────────────────────

async def message_counter(update: Update, context: CallbackContext) -> None:
    if await _maintenance_enabled():
        return
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if not chat or chat.type == "private" or not message or not user:
        return
    # Bot-generated text and service updates must not keep a quiet group active.
    if user.is_bot or not (message.text or "").strip():
        return

    chat_id = chat.id
    user_id = user.id

    last = _last_user.get(chat_id)
    if last and last["user_id"] == user_id:
        last["count"] += 1
        if last["count"] >= 10:
            warned_at = _warned.get(user_id, 0)
            if time.time() - warned_at < 600:
                return
            _warned[user_id] = time.time()
            try:
                await message.reply_text(
                    f"⚠️ {escape(user.first_name)}, slow down!\n"
                    f"Your messages will be ignored for 10 minutes."
                )
            except Exception:
                pass
            return
    else:
        _last_user[chat_id] = {"user_id": user_id, "count": 1}

    # Register this group as active only after its message passes the spam guard.
    _registered_chats.add(chat_id)
    _last_activity[chat_id] = time.time()
    # Deliberately do not send drops from this handler. Drops are generated
    # only by _timed_drop_job at Config.DROP_INTERVAL_MIN, in active groups.


# ── /guess ────────────────────────────────────────────────────────────────────

async def guess(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    bot     = context.bot

    char = _active_char.get(chat_id)
    if not char:
        msg = await _safe_send(
            bot, chat_id,
            "❌ There is no active character to guess right now. Wait for the next drop!",
        )
        if msg:
            schedule_delete(bot, chat_id, msg.message_id, WRONG_GUESS_DELAY)
        schedule_delete(bot, chat_id, update.message.message_id, WRONG_GUESS_DELAY)
        return

    if chat_id in _claimed:
        msg = await _safe_send(
            bot, chat_id,
            "❌ Already claimed by someone else! Wait for the next character.",
        )
        if msg:
            schedule_delete(bot, chat_id, msg.message_id, WRONG_GUESS_DELAY)
        schedule_delete(bot, chat_id, update.message.message_id, WRONG_GUESS_DELAY)
        return

    user_guess = " ".join(context.args).strip().lower() if context.args else ""
    if not user_guess:
        msg = await _safe_send(bot, chat_id, "Usage: /guess <character name>")
        if msg:
            schedule_delete(bot, chat_id, msg.message_id, WRONG_GUESS_DELAY)
        schedule_delete(bot, chat_id, update.message.message_id, WRONG_GUESS_DELAY)
        return

    if any(bad in user_guess for bad in ("()", "&&", "||", "<script")):
        msg = await _safe_send(bot, chat_id, "❌ Invalid characters in guess.")
        if msg:
            schedule_delete(bot, chat_id, msg.message_id, WRONG_GUESS_DELAY)
        schedule_delete(bot, chat_id, update.message.message_id, WRONG_GUESS_DELAY)
        return

    # Normalize case, accents and punctuation before comparing character names.
    def normalize_name(value: str) -> str:
        value = unicodedata.normalize("NFKD", value.casefold())
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return " ".join(re.findall(r"[a-z0-9]+", value))

    expected = normalize_name(str(char.get("name", "")))
    guessed = normalize_name(user_guess)
    name_parts = expected.split()
    correct = bool(guessed) and (
        guessed == expected
        or (len(guessed.split()) == 1 and guessed in name_parts)
    )

    if not correct:
        schedule_delete(bot, chat_id, update.message.message_id, WRONG_GUESS_DELAY)
        msg = await _safe_send(bot, chat_id, "❌ Wrong! Look at the character again 👀")
        if msg:
            schedule_delete(bot, chat_id, msg.message_id, WRONG_GUESS_DELAY)
        return

    # ── Correct guess ─────────────────────────────────────────────────────────
    _claimed[chat_id] = user_id
    _active_char.pop(chat_id, None)

    drop_time  = _active_time.pop(chat_id, time.time())
    time_taken = int(time.time() - drop_time)

    msg_id = _active_msg.pop(chat_id, None)
    if msg_id:
        # Delete the claimed drop immediately.
        await delete_now(bot, chat_id, msg_id, ignore_errors=True)

    schedule_delete(bot, chat_id, update.message.message_id, GUESS_COMMAND_DELAY)

    u = update.effective_user
    try:
        game_doc = await db["miniapp_settings"].find_one({"_id": "game"})
        game_settings = (game_doc or {}).get("data") or {}
        xp_reward = max(0, min(1000000, int(game_settings.get("guess_xp_reward", _XP_PER_GUESS))))
    except (TypeError, ValueError):
        xp_reward = _XP_PER_GUESS
    try:
        economy_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
        economy_settings = (economy_doc or {}).get("data") or {}
        starting_coins = max(0, min(100000000, int(economy_settings.get("starting_coins", 0))))
    except (TypeError, ValueError):
        starting_coins = 0

    active_events = await _active_event_details()
    char_rarity_keys = _rarity_keys(_rarity_label(char.get("rarity", Config.RARITY_MAP[1])))
    event = next((active_events[key] for key in char_rarity_keys if key in active_events), None)
    try:
        event_bonus = max(0, min(100000000, int((event or {}).get("reward", 0))))
    except (TypeError, ValueError):
        event_bonus = 0
    event_bonus_text = f"\n🎉 Event reward: +{event_bonus:,} 🟡 Neko Coins" if event_bonus else ""
    # Seed new accounts in a separate upsert, avoiding MongoDB operator
    # conflicts between $inc and $setOnInsert on the coins field.
    await user_collection.update_one(
        {"id": user_id},
        {
            "$set": {"username": u.username, "first_name": u.first_name},
            "$setOnInsert": {
                "coins": starting_coins, "wins": 0, "favorites": [],
                "characters": [], "xp": 0, "total_guesses": 0,
            },
        },
        upsert=True,
    )
    await user_collection.update_one(
        {"id": user_id},
        {
            "$push": {"characters": char},
            "$inc": {"total_guesses": 1, "xp": xp_reward, "coins": event_bonus},
        },
    )

    await group_user_totals_collection.update_one(
        {"user_id": user_id, "group_id": chat_id},
        {"$set": {"username": u.username, "first_name": u.first_name}, "$inc": {"count": 1}},
        upsert=True,
    )
    await top_global_groups_collection.update_one(
        {"group_id": chat_id},
        {"$set": {"group_name": update.effective_chat.title}, "$inc": {"count": 1}},
        upsert=True,
    )

    rarity     = _rarity_label(char.get("rarity", Config.RARITY_MAP[1]))
    is_special = char.get("is_special", False)
    special_tag = "🌟 <b>SPECIAL CHARACTER CLAIMED!</b>\n\n" if is_special else ""

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📖 My Harem",
            switch_inline_query_current_chat=f"collection.{user_id}",
        )
    ]])

    await _safe_send(
        bot, chat_id,
        f'{special_tag}'
        f'👑 <b>𝗡𝗘𝗪 𝗖𝗛𝗔𝗥𝗔𝗖𝗧𝗘𝗥 𝗖𝗔𝗣𝗧𝗨𝗥𝗘𝗗</b> 👑\n\n'
        f'🎯 <a href="tg://user?id={user_id}">{escape(u.first_name)}</a> claimed this character!\n\n'
        f'🌸 {escape(char["name"])}\n'
        f'🎬 {escape(char["anime"])}\n'
        f'💠 {rarity}\n'
        f'⏱️ {time_taken} seconds\n\n'
        f'✨ Harem Updated! +{xp_reward} XP 🔥'
        f'{event_bonus_text}',
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


# ── /fav ──────────────────────────────────────────────────────────────────────

async def fav(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /fav <character_id>")
        return

    char_id  = context.args[0]
    user_doc = await user_collection.find_one({"id": user_id})
    if not user_doc:
        await update.message.reply_text("You haven't guessed any characters yet.")
        return

    char = next((c for c in user_doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text("That character isn't in your collection.")
        return

    await user_collection.update_one({"id": user_id}, {"$set": {"favorites": [char_id]}})
    await update.message.reply_text(
        f"⭐ <b>{escape(char['name'])}</b> set as your favourite!",
        parse_mode=ParseMode.HTML,
    )


# ── /setspecial ───────────────────────────────────────────────────────────────

async def setspecial(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/setspecial &lt;char_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id = context.args[0].strip()
    char    = await collection.find_one({"id": char_id})
    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{char_id}</code> not found in DB!",
            parse_mode=ParseMode.HTML,
        )
        return

    existing = await special_collection.find_one({"char_id": char_id})
    if existing:
        await update.message.reply_text(
            f"⚠️ <b>{escape(char['name'])}</b> is already in special list!",
            parse_mode=ParseMode.HTML,
        )
        return

    await special_collection.insert_one({
        "char_id": char_id,
        "name":    char["name"],
        "anime":   char["anime"],
        "rarity":  _rarity_label(char.get("rarity", Config.RARITY_MAP[1])),
    })

    await update.message.reply_text(
        f"🌟 <b>{escape(char['name'])}</b> added to special drop list!\n"
        f"💎 Rarity: {_rarity_label(char.get('rarity', Config.RARITY_MAP[1]))}\n"
        f"🎯 Drop chance: 0.1% of the normal rarity weight",
        parse_mode=ParseMode.HTML,
    )


# ── /removespecial ────────────────────────────────────────────────────────────

async def removespecial(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/removespecial &lt;char_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id = context.args[0].strip()
    result  = await special_collection.delete_one({"char_id": char_id})

    if result.deleted_count:
        await update.message.reply_text(
            f"✅ Character <code>{char_id}</code> removed from special list!",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("❌ Character not found in special list!")


# ── /speciallist ──────────────────────────────────────────────────────────────

async def speciallist(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return

    specials = await special_collection.find({}).to_list(length=100)
    if not specials:
        await update.message.reply_text("📭 No special characters set yet!")
        return

    lines = ["🌟 <b>Special Characters List</b>\n"]
    for s in specials:
        lines.append(
            f"🎴 <b>{escape(s['name'])}</b> — {_rarity_label(s.get('rarity', Config.RARITY_MAP[1]))}\n"
            f"   🆔 <code>{s['char_id']}</code>"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler(
    ["guess", "protecc", "collect", "grab", "hunt"], guess, block=False
))
application.add_handler(CommandHandler("fav",           fav,           block=False))
application.add_handler(CommandHandler("setspecial",    setspecial,    block=False))
application.add_handler(CommandHandler("removespecial", removespecial, block=False))
application.add_handler(CommandHandler("speciallist",   speciallist,   block=False))
application.add_handler(MessageHandler(
    filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
    message_counter,
    block=False,
))
