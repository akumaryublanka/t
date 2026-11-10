"""Authenticated Telegram Mini App API for AdamWaifusbot."""
import aiohttp
import asyncio
import copy
from collections import OrderedDict
import uuid
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import os
import random
import time
from urllib.parse import parse_qsl

from aiohttp import web
from bson import ObjectId
from telegram import InputFile

from waifu import TOKEN, application, CHARA_CHANNEL_ID, collection, user_collection, market_collection, db
from waifu.image_watermark import watermark_image_bytes
from waifu.config import Config

MAX_INIT_DATA_AGE = 86_400
MINES_COUNT = 3
GRID_SIZE = 4
TOTAL_TILES = GRID_SIZE * GRID_SIZE
logger = logging.getLogger(__name__)
QUIZ_REWARD = 80
QUIZ_XP = 15
EXTRA_GAME_REWARD = 50
EXTRA_GAME_XP = 10
_RARITY_DUEL_REWARD = 60
_RARITY_DUEL_XP = 12
_MEMORY_MATCH_REWARD = 120
_MEMORY_MATCH_XP = 25
_CHARACTER_IMAGE_CACHE: OrderedDict[tuple[str, str], tuple[float, bytes, str]] = OrderedDict()
_CHARACTER_IMAGE_CACHE_BYTES = 0
_CHARACTER_IMAGE_CACHE_MAX_BYTES = 24 * 1024 * 1024
_CHARACTER_IMAGE_CACHE_MAX_ITEM_BYTES = 5 * 1024 * 1024
_CHARACTER_IMAGE_CACHE_TTL = 6 * 60 * 60
_CHARACTER_IMAGE_CACHE_LOCK = asyncio.Lock()
_USER_AVATAR_CACHE: OrderedDict[int, tuple[float, bytes, str]] = OrderedDict()
_USER_AVATAR_CACHE_BYTES = 0
_USER_AVATAR_CACHE_MAX_BYTES = 8 * 1024 * 1024
_USER_AVATAR_CACHE_MAX_ITEM_BYTES = 1024 * 1024
_USER_AVATAR_CACHE_TTL = 15 * 60
_USER_AVATAR_CACHE_LOCK = asyncio.Lock()
_rarity_duel_sessions: dict[str, dict] = {}
_memory_match_sessions: dict[str, dict] = {}
_ADMIN_SETTINGS_CACHE: dict | None = None
_ADMIN_SETTINGS_CACHE_AT = 0.0
_ADMIN_SETTINGS_CACHE_TTL = 10.0
_ADMIN_SETTINGS_CACHE_LOCK = asyncio.Lock()
_quiz_sessions: dict[str, dict] = {}
_extra_game_sessions: dict[str, dict] = {}
_mines_games: dict[int, dict] = {}
_API_RATE_WINDOWS: dict[int, list[float]] = {}
_API_RATE_LIMIT = 120  # authenticated API calls per user per rolling minute


def _allowed_origins() -> set[str]:
    # Keep the deployed Mini App origin available even if a stale Render
    # environment variable was set during an earlier deployment.
    configured = os.environ.get("MINIAPP_ORIGIN", "")
    extras = os.environ.get("MINIAPP_ALLOWED_ORIGINS", "")
    origins = {
        "https://nekoverse-miniapp.onrender.com",
        *(value.strip().rstrip("/") for value in configured.split(",")),
        *(value.strip().rstrip("/") for value in extras.split(",")),
    }
    return {origin for origin in origins if origin}


@web.middleware
async def miniapp_cors(request, handler):
    origin = request.headers.get("Origin")
    normalized_origin = origin.rstrip("/") if origin else None
    allowed_origins = _allowed_origins()
    if normalized_origin and normalized_origin not in allowed_origins:
        return web.json_response({"error": "Origin not allowed"}, status=403)
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            # aiohttp uses HTTP exceptions for expected 401/403/404/etc.
            # Convert them to responses here so the CORS headers below are
            # still attached; otherwise browsers hide the useful JSON error
            # and report only "Failed to fetch".
            response = exc
    if normalized_origin and normalized_origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = normalized_origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    # Preserve deliberate image caching. JSON/API responses remain no-store.
    if not response.headers.get("Cache-Control"):
        response.headers["Cache-Control"] = "no-store"
    return response


def verify_init_data(init_data: str, bot_token: str | None = None, now: int | None = None) -> dict:
    """Validate Telegram WebApp initData and return its authenticated user payload."""
    if not init_data or len(init_data) > 16_384:
        raise ValueError("Missing or invalid Telegram initData")
    token = bot_token or TOKEN
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError("Duplicate initData fields")
        received_hash = values.pop("hash", None)
        if not received_hash or len(received_hash) != 64:
            raise ValueError("Missing Telegram signature")
        check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            raise ValueError("Invalid Telegram signature")
        auth_date = int(values.get("auth_date", "0"))
        current = int(time.time() if now is None else now)
        if auth_date > current + 30 or current - auth_date > MAX_INIT_DATA_AGE:
            raise ValueError("Telegram sign-in expired. Reopen the Mini App.")
        user = json.loads(values.get("user", "{}"))
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            raise ValueError("Telegram user is missing")
        return user
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Malformed Telegram initData") from exc


async def _identity(request: web.Request) -> dict:
    raw = request.headers.get("X-Telegram-Init-Data", "")
    try:
        tg_user = verify_init_data(raw)
    except ValueError as exc:
        raise web.HTTPUnauthorized(text=json.dumps({"error": str(exc)}), content_type="application/json")
    # The Owner Console remains accessible to the owner during maintenance.
    if request.path.startswith("/api/miniapp/") and request.path != "/api/miniapp/public-config":
        try:
            owner_id = int(os.environ.get("OWNER_ID", "0"))
        except (TypeError, ValueError):
            owner_id = 0
        if tg_user.get("id") != owner_id and hasattr(db, "__getitem__"):
            cached_settings = await _get_admin_settings()
            security = cached_settings.get("security", {})
            if security.get("rate_limit_enabled", True) is not False:
                now = time.monotonic()
                uid = tg_user["id"]
                recent = [tick for tick in _API_RATE_WINDOWS.get(uid, []) if now - tick < 60]
                if len(recent) >= _API_RATE_LIMIT:
                    raise web.HTTPTooManyRequests(
                        text=json.dumps({"error": "Too many requests. Please wait a moment and retry."}),
                        content_type="application/json",
                        headers={"Retry-After": "60"},
                    )
                recent.append(now)
                _API_RATE_WINDOWS[uid] = recent
                if len(_API_RATE_WINDOWS) > 10000:
                    for old_uid, timestamps in list(_API_RATE_WINDOWS.items()):
                        if not timestamps or now - timestamps[-1] >= 60:
                            _API_RATE_WINDOWS.pop(old_uid, None)
        if tg_user.get("id") != owner_id:
            if hasattr(user_collection, "find_one"):
                record = await user_collection.find_one(
                    {"id": tg_user["id"]}, {"miniapp_suspended": 1}
                )
                if record and record.get("miniapp_suspended"):
                    raise web.HTTPForbidden(
                        text=json.dumps({"error": "Mini App access is paused for this account. Contact the bot owner."}),
                        content_type="application/json",
                    )
            if hasattr(db, "__getitem__"):
                values = cached_settings.get("general", {})
                if values.get("maintenance_mode") is True:
                    message = str(values.get("maintenance_message") or _DEFAULT_ADMIN_SETTINGS["general"]["maintenance_message"])
                    raise web.HTTPServiceUnavailable(text=json.dumps({"error": message}), content_type="application/json")
    return tg_user


def _display_rarity(value) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return Config.RARITY_MAP.get(value, Config.RARITY_MAP[1])
    raw = str(value or Config.RARITY_MAP[1]).strip()
    if raw.isdigit():
        return Config.RARITY_MAP.get(int(raw), Config.RARITY_MAP[1])
    return Config.RARITY_LEGACY_MAP.get(raw, raw)


def _public_character(char: dict) -> dict:
    char_id = str(char.get("id", ""))
    image_ref = str(char.get("img_url", "") or "").strip()
    # Always use the proxy for known character IDs. It can recover direct
    # Telegram uploads and older records whose third-party image URLs expired.
    if char_id and image_ref:
        image_ref = f"https://t-5vdc.onrender.com/api/miniapp/character-image/{char_id}?wm=1"
    return {
        "id": char_id,
        "name": str(char.get("name", "Unknown")),
        "anime": str(char.get("anime", "Unknown")),
        "rarity": _display_rarity(char.get("rarity")),
        "img_url": image_ref,
    }


async def api_me(request: web.Request) -> web.Response:
    """Return a compact profile payload; collection data is loaded separately on demand."""
    tg_user = await _identity(request)
    uid = tg_user["id"]
    settings = await _get_admin_settings()
    try:
        starting_coins = max(0, min(100000000, int(settings.get("economy", {}).get("starting_coins", 0))))
    except (TypeError, ValueError):
        starting_coins = 0
    user = await user_collection.find_one_and_update(
        {"id": uid},
        {"$set": {"username": tg_user.get("username"), "first_name": tg_user.get("first_name", "Collector"),
                  "last_miniapp_active": int(time.time())},
         "$setOnInsert": {"id": uid, "characters": [], "coins": starting_coins, "xp": 0, "wins": 0, "favorites": [],
                          "created_at": int(time.time())}},
        projection={"_id": 0, "id": 1, "first_name": 1, "username": 1, "coins": 1, "grey_shards": 1, "xp": 1, "wins": 1},
        upsert=True,
        return_document=True,
    )
    counts = await user_collection.aggregate([
        {"$match": {"id": uid}},
        {"$project": {"_id": 0, "character_count": {"$size": {"$ifNull": ["$characters", []]}}}},
    ]).to_list(length=1)
    character_count = int((counts[0] if counts else {}).get("character_count", 0) or 0)
    profile = {
        "id": uid,
        "first_name": user.get("first_name") or tg_user.get("first_name", "Collector"),
        "username": user.get("username") or tg_user.get("username"),
        # Telegram includes photo_url in the signed WebAppUser payload when
        # this launch mode/user privacy settings make it available.
        "photo_url": str(tg_user.get("photo_url") or ""),
        "coins": int(user.get("coins", 0) or 0),
        "grey_shards": int(user.get("grey_shards", 0) or 0),
        "xp": int(user.get("xp", 0) or 0),
        "wins": int(user.get("wins", 0) or 0),
        "character_count": character_count,
    }
    return web.json_response({"profile": profile})


async def api_user_avatar(request: web.Request) -> web.StreamResponse:
    """Return the authenticated Telegram user's profile photo without exposing the bot token."""
    global _USER_AVATAR_CACHE_BYTES
    tg_user = await _identity(request)
    try:
        requested_uid = int(request.match_info.get("user_id", ""))
    except (TypeError, ValueError):
        raise web.HTTPNotFound(text="Profile photo not found")
    if requested_uid != int(tg_user["id"]):
        raise web.HTTPForbidden(text="You can only request your own profile photo")

    now = time.monotonic()
    async with _USER_AVATAR_CACHE_LOCK:
        cached = _USER_AVATAR_CACHE.get(requested_uid)
        if cached:
            expires_at, image_bytes, content_type = cached
            if expires_at > now:
                _USER_AVATAR_CACHE.move_to_end(requested_uid)
                return web.Response(
                    body=image_bytes,
                    content_type=content_type,
                    headers={"Cache-Control": "private, max-age=900, stale-while-revalidate=3600"},
                )
            _USER_AVATAR_CACHE.pop(requested_uid, None)
            _USER_AVATAR_CACHE_BYTES -= len(image_bytes)

    try:
        photos = await application.bot.get_user_profile_photos(user_id=requested_uid, limit=1)
        if not photos.photos or not photos.photos[0]:
            raise web.HTTPNotFound(text="This Telegram account has no profile photo available")
        largest_photo = photos.photos[0][-1]
        telegram_file = await application.bot.get_file(largest_photo.file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        if not image_bytes:
            raise web.HTTPBadGateway(text="Telegram returned an empty profile photo")

        file_path = str(getattr(telegram_file, "file_path", "") or "").lower()
        content_type = "image/png" if file_path.endswith(".png") else (
            "image/webp" if file_path.endswith(".webp") else "image/jpeg"
        )
        if len(image_bytes) <= _USER_AVATAR_CACHE_MAX_ITEM_BYTES:
            async with _USER_AVATAR_CACHE_LOCK:
                previous = _USER_AVATAR_CACHE.pop(requested_uid, None)
                if previous:
                    _USER_AVATAR_CACHE_BYTES -= len(previous[1])
                _USER_AVATAR_CACHE[requested_uid] = (
                    time.monotonic() + _USER_AVATAR_CACHE_TTL, image_bytes, content_type
                )
                _USER_AVATAR_CACHE_BYTES += len(image_bytes)
                while _USER_AVATAR_CACHE and _USER_AVATAR_CACHE_BYTES > _USER_AVATAR_CACHE_MAX_BYTES:
                    _, evicted = _USER_AVATAR_CACHE.popitem(last=False)
                    _USER_AVATAR_CACHE_BYTES -= len(evicted[1])

        return web.Response(
            body=image_bytes,
            content_type=content_type,
            headers={"Cache-Control": "private, max-age=900, stale-while-revalidate=3600"},
        )
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "Could not load Telegram profile photo for user %s (%s)",
            requested_uid, type(exc).__name__,
        )
        raise web.HTTPBadGateway(text="Telegram profile photo could not be loaded") from exc


async def api_user_collection(request: web.Request) -> web.Response:
    """Paginated collection endpoint so profile/game refreshes never send the whole harem."""
    tg_user = await _identity(request)
    try:
        offset = max(0, min(1_000_000, int(request.query.get("offset", "0"))))
        limit = max(1, min(100, int(request.query.get("limit", "100"))))
    except (TypeError, ValueError):
        offset, limit = 0, 100
    user = await user_collection.find_one(
        {"id": tg_user["id"]},
        {"_id": 0, "characters": {"$slice": [offset, limit]}},
    )
    characters = (user or {}).get("characters", []) or []
    return web.json_response({
        "characters": [_public_character(char) for char in characters],
        "offset": offset,
        "limit": limit,
        "has_more": len(characters) == limit,
    })

async def api_market(request: web.Request) -> web.Response:
    await _identity(request)
    await _require_feature("market")
    rows = await market_collection.find({}).sort("price", 1).limit(40).to_list(40)
    listings = []
    for item in rows:
        char = item.get("char") or {}
        listings.append({
            "id": str(item.get("_id", "")),
            "seller_id": int(item.get("seller_id", 0)),
            "seller_name": str(item.get("seller_name", "Collector")),
            "price": max(0, int(item.get("price", 0) or 0)),
            "char": _public_character(char),
        })
    return web.json_response({"listings": listings})


async def api_market_buy(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("market")
    uid = tg_user["id"]
    body = await request.json()
    listing_id = str(body.get("listing_id", ""))
    if not ObjectId.is_valid(listing_id):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid listing ID"}), content_type="application/json")
    listing = await market_collection.find_one({"_id": ObjectId(listing_id)})
    if not listing:
        raise web.HTTPNotFound(text=json.dumps({"error": "Listing not found"}), content_type="application/json")
    if int(listing.get("seller_id", 0)) == uid:
        raise web.HTTPBadRequest(text=json.dumps({"error": "You cannot buy your own listing"}), content_type="application/json")
    price = max(0, int(listing.get("price", 0) or 0))
    settings = await _get_admin_settings()
    economy_settings = settings.get("economy", {})
    try:
        fee_percent = max(0, min(100, int(economy_settings.get("market_fee_percent", 0))))
    except (TypeError, ValueError):
        fee_percent = 0
    fee = min(price, (price * fee_percent) // 100)
    seller_payout = price - fee
    # Remove listing first so two buyers cannot claim the same character.
    claimed = await market_collection.find_one_and_delete({"_id": listing["_id"]})
    if not claimed:
        raise web.HTTPConflict(text=json.dumps({"error": "This listing was just purchased"}), content_type="application/json")
    debit = await user_collection.update_one({"id": uid, "coins": {"$gte": price}}, {"$inc": {"coins": -price}, "$push": {"characters": listing["char"]}})
    if not debit.modified_count:
        await market_collection.insert_one(claimed)
        raise web.HTTPPaymentRequired(text=json.dumps({"error": f"Not enough 🟡 Neko Coins. Need {price:,} 🟡 Neko Coins."}), content_type="application/json")
    if seller_payout:
        await user_collection.update_one({"id": int(listing["seller_id"])}, {"$inc": {"coins": seller_payout}})
    return web.json_response({"ok": True, "message": "Character purchased!", "price": price, "market_fee": fee, "seller_payout": seller_payout})


async def api_store_list(request: web.Request) -> web.Response:
    """List unsold official store characters. Store inventory is separate from drop inventory."""
    # Keep inventory in its own collection: normal drop code reads anime_characters only.
    tg_user = await _identity(request)
    rows = await db["miniapp_store_characters"].find(
        {"active": {"$ne": False}, "sold": {"$ne": True}}
    ).sort("created_at", -1).limit(100).to_list(100)
    items = []
    for char in rows:
        public = _public_character(char)
        public.update({
            "price": max(1, int(char.get("price", 1) or 1)),
            "currency": char.get("currency") if char.get("currency") in {"coins", "grey_shards"} else "coins",
            "purchase_mode": char.get("purchase_mode") if char.get("purchase_mode") in {"multiple", "single"} else "multiple",
        })
        items.append(public)
    wallet = await user_collection.find_one({"id": int(tg_user["id"])}, {"coins": 1, "grey_shards": 1}) or {}
    return web.json_response({"characters": items, "wallet": {"coins": int(wallet.get("coins", 0) or 0), "grey_shards": int(wallet.get("grey_shards", 0) or 0)}})


async def api_store_buy(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    uid = int(tg_user["id"])
    try:
        body = await request.json()
        char_id = str(body.get("character_id", "")).strip()[:32]
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid purchase request."}), content_type="application/json")
    if not char_id:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Character ID is required."}), content_type="application/json")

    inventory = db["miniapp_store_characters"]
    char = await inventory.find_one({"id": char_id, "active": {"$ne": False}, "sold": {"$ne": True}})
    if not char:
        raise web.HTTPConflict(text=json.dumps({"error": "This store character is sold out or unavailable."}), content_type="application/json")
    price = max(1, min(1_000_000_000, int(char.get("price", 1) or 1)))
    currency = char.get("currency") if char.get("currency") in {"coins", "grey_shards"} else "coins"
    balance_field = "grey_shards" if currency == "grey_shards" else "coins"
    balance_label = "💎 Neko Gems" if balance_field == "grey_shards" else "🟡 Neko Coins"
    mode = char.get("purchase_mode") if char.get("purchase_mode") in {"multiple", "single"} else "multiple"

    # Claim one-time stock atomically before charging, preventing two buyers from
    # purchasing a single-stock character at the same time.
    claimed = None
    if mode == "single":
        claimed = await inventory.find_one_and_update(
            {"_id": char["_id"], "active": {"$ne": False}, "sold": {"$ne": True}},
            {"$set": {"sold": True, "sold_to": uid, "sold_at": int(time.time())}},
            return_document=True,
        )
        if not claimed:
            raise web.HTTPConflict(text=json.dumps({"error": "This character has just been sold."}), content_type="application/json")

    character_copy = {key: char.get(key) for key in ("id", "name", "anime", "rarity", "img_url", "message_id") if char.get(key) is not None}
    debit = await user_collection.update_one(
        {"id": uid, balance_field: {"$gte": price}},
        {"$inc": {balance_field: -price}, "$push": {"characters": character_copy}},
    )
    if not debit.modified_count:
        if mode == "single":
            await inventory.update_one(
                {"_id": char["_id"], "sold_to": uid},
                {"$set": {"sold": False}, "$unset": {"sold_to": "", "sold_at": ""}},
            )
        raise web.HTTPPaymentRequired(
            text=json.dumps({"error": f"Not enough {balance_label}. Need {price:,}."}),
            content_type="application/json",
        )

    await _write_admin_audit(
        tg_user, "store.purchase", char_id,
        {"price": price, "currency": currency, "purchase_mode": mode},
    )
    user = await user_collection.find_one({"id": uid}, {"coins": 1, "grey_shards": 1})
    return web.json_response({
        "ok": True, "message": f"Purchased {char.get('name', 'character')}!",
        "character_id": char_id, "price": price, "currency": currency,
        "coins": int((user or {}).get("coins", 0) or 0),
        "grey_shards": int((user or {}).get("grey_shards", 0) or 0),
    })


async def _store_admin_identity(request: web.Request) -> dict:
    tg_user = await _identity(request)
    try:
        owner_id = int(os.environ.get("OWNER_ID", "0"))
    except (TypeError, ValueError):
        owner_id = 0
    # The owner and configured bot sudo/admin users may manage the store.
    try:
        from waifu import sudo_users
        admin_ids = {int(value) for value in sudo_users}
    except Exception:
        admin_ids = set()
    if int(tg_user.get("id", 0)) != owner_id and int(tg_user.get("id", 0)) not in admin_ids:
        raise web.HTTPForbidden(text=json.dumps({"error": "Owner/Admin access only."}), content_type="application/json")
    return tg_user


async def api_admin_store_characters(request: web.Request) -> web.Response:
    actor = await _store_admin_identity(request)
    inventory = db["miniapp_store_characters"]
    if request.method == "GET":
        query = request.query.get("q", "").strip()
        filt = {}
        if query:
            safe = query.replace("\\", "\\\\").replace(".", "\\.").replace("$", "\\$")
            filt = {"$or": [
                {"id": {"$regex": safe, "$options": "i"}},
                {"name": {"$regex": safe, "$options": "i"}},
                {"anime": {"$regex": safe, "$options": "i"}},
            ]}
        rows = await inventory.find(filt).sort("created_at", -1).limit(200).to_list(200)
        result = []
        for char in rows:
            item = _admin_character(char)
            item.update({
                "price": int(char.get("price", 1) or 1),
                "currency": char.get("currency", "coins"),
                "purchase_mode": char.get("purchase_mode", "multiple"),
                "active": char.get("active", True) is not False,
                "sold": char.get("sold", False) is True,
                "sold_to": char.get("sold_to"),
            })
            result.append(item)
        return web.json_response({"characters": result})

    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()[:100]
        anime = str(body.get("anime", "")).strip()[:100]
        rarity = str(body.get("rarity", "⚪ Common")).strip()[:80]
        image_ref = str(body.get("img_url", "")).strip()[:2000]
        price = int(body.get("price", 0))
        currency = str(body.get("currency", "coins"))
        mode = str(body.get("purchase_mode", "multiple"))
        active = body.get("active", True) is not False
    except (ValueError, TypeError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid store character fields."}), content_type="application/json")
    if not name or not anime:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Character name and anime are required."}), content_type="application/json")
    if price < 1 or price > 1_000_000_000:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Price must be between 1 and 1,000,000,000."}), content_type="application/json")
    if currency not in {"coins", "grey_shards"}:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Choose 🟡 Neko Coins or 💎 Neko Gems."}), content_type="application/json")
    if mode not in {"multiple", "single"}:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Choose multiple or single purchase mode."}), content_type="application/json")
    if image_ref.startswith("//"):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Use a full HTTPS image URL or Telegram file ID."}), content_type="application/json")
    counter = await db.sequences.find_one_and_update(
        {"_id": "store_character_id"}, {"$inc": {"sequence_value": 1}},
        upsert=True, return_document=True,
    )
    char_id = "S" + str(counter.get("sequence_value", 1)).zfill(5)
    doc = {
        "id": char_id, "name": name, "anime": anime, "rarity": rarity,
        "img_url": image_ref, "price": price, "currency": currency,
        "purchase_mode": mode, "active": active, "sold": False,
        "created_at": int(time.time()), "created_by": int(actor["id"]),
    }
    await inventory.insert_one(doc)
    await _write_admin_audit(actor, "store.character.create", char_id, {"name": name, "price": price, "currency": currency, "purchase_mode": mode})
    doc.pop("_id", None)
    return web.json_response({"ok": True, "character": doc}, status=201)


async def api_admin_store_character(request: web.Request) -> web.Response:
    actor = await _store_admin_identity(request)
    char_id = str(request.match_info.get("char_id", "")).strip()
    inventory = db["miniapp_store_characters"]
    char = await inventory.find_one({"id": char_id})
    if not char:
        raise web.HTTPNotFound(text=json.dumps({"error": "Store character not found."}), content_type="application/json")
    if request.method == "DELETE":
        if char.get("sold") is True:
            raise web.HTTPConflict(text=json.dumps({"error": "Already-sold store characters cannot be deleted; disable them instead."}), content_type="application/json")
        await inventory.delete_one({"_id": char["_id"]})
        await _write_admin_audit(actor, "store.character.delete", char_id, {"name": char.get("name", "")})
        return web.json_response({"ok": True, "id": char_id})
    try:
        body = await request.json()
        updates = {}
        for field, limit in (("name", 100), ("anime", 100), ("rarity", 80), ("img_url", 2000)):
            if field in body:
                value = str(body[field]).strip()[:limit]
                if field in {"name", "anime"} and not value:
                    raise ValueError(f"{field} cannot be empty.")
                if field == "img_url" and value.startswith("//"):
                    raise ValueError("Use a full HTTPS image URL or Telegram file ID.")
                updates[field] = value
        if "price" in body:
            price = int(body["price"])
            if not 1 <= price <= 1_000_000_000:
                raise ValueError("Price must be between 1 and 1,000,000,000.")
            updates["price"] = price
        if "currency" in body:
            if body["currency"] not in {"coins", "grey_shards"}:
                raise ValueError("Choose 🟡 Neko Coins or 💎 Neko Gems.")
            updates["currency"] = body["currency"]
        if "purchase_mode" in body:
            if body["purchase_mode"] not in {"multiple", "single"}:
                raise ValueError("Choose multiple or single purchase mode.")
            if char.get("sold") is True and body["purchase_mode"] == "multiple":
                raise ValueError("A sold character cannot be switched to multiple-sale mode.")
            updates["purchase_mode"] = body["purchase_mode"]
        if "active" in body:
            updates["active"] = body["active"] is True
    except (ValueError, TypeError, AttributeError) as exc:
        raise web.HTTPBadRequest(text=json.dumps({"error": str(exc) or "Invalid store character fields."}), content_type="application/json")
    if not updates:
        raise web.HTTPBadRequest(text=json.dumps({"error": "No editable fields provided."}), content_type="application/json")
    await inventory.update_one({"_id": char["_id"]}, {"$set": updates})
    char.update(updates)
    await _write_admin_audit(actor, "store.character.update", char_id, {"fields": sorted(updates)})
    char.pop("_id", None)
    return web.json_response({"ok": True, "character": char})


async def _sample_game_characters(size: int = 250) -> list[dict]:
    """Randomly sample a bounded pool rather than downloading the entire catalogue."""
    bounded_size = max(20, min(300, int(size)))
    return await collection.aggregate([
        {"$match": {"img_url": {"$exists": True, "$nin": ["", None]}}},
        {"$sample": {"size": bounded_size}},
    ]).to_list(length=bounded_size)


def _normalize_game_answer(value: object) -> str:
    return "".join(char.casefold() for char in str(value or "")[:200] if char.isalnum())


def _rarity_rank_lookup(settings: dict) -> dict[str, int]:
    rarities = settings.get("rarities", [])
    rank = {}
    main_index = 0
    event_index = 0
    for item in rarities if isinstance(rarities, list) else []:
        if not isinstance(item, dict):
            continue
        name = _normalize_game_answer(item.get("name", ""))
        if not name:
            continue
        if item.get("kind") == "event":
            rank[name] = 1000 + event_index
            event_index += 1
        else:
            rank[name] = main_index
            main_index += 1
    return rank


def _character_rarity_rank(char: dict, rank_lookup: dict[str, int]) -> int:
    raw = str(char.get("rarity", "") or "Common")
    normalized = _normalize_game_answer(raw)
    for name, rank in rank_lookup.items():
        if normalized.endswith(name):
            return rank
    return rank_lookup.get(normalized, 0)


async def api_rarity_duel_start(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    settings = await _get_admin_settings()
    rank_lookup = _rarity_rank_lookup(settings)
    chars = await _sample_game_characters(300)
    ranked = []
    seen = set()
    for char in chars:
        name = str(char.get("name", "")).strip()
        anime = str(char.get("anime", "")).strip()
        if not name or not anime:
            continue
        identity = str(char.get("id", "") or (name.casefold() + "|" + anime.casefold()))
        if identity in seen:
            continue
        seen.add(identity)
        ranked.append((char, _character_rarity_rank(char, rank_lookup)))
    if len(ranked) < 2:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Not enough character cards are available for a duel right now."}),
            content_type="application/json",
        )
    different = [(left, right) for left in range(len(ranked)) for right in range(left + 1, len(ranked))
                 if ranked[left][1] != ranked[right][1]]
    if not different:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "A rarity duel needs characters with different rarity tiers."}),
            content_type="application/json",
        )
    left_index, right_index = random.choice(different)
    (left, left_rank), (right, right_rank) = ranked[left_index], ranked[right_index]
    cards = [left, right]
    correct_index = 0 if left_rank > right_rank else 1
    question_id = hashlib.sha256(
        f"{tg_user['id']}:rarity-duel:{time.time_ns()}:{random.random()}".encode()
    ).hexdigest()[:24]
    _rarity_duel_sessions[question_id] = {
        "uid": tg_user["id"], "cards": cards, "ranks": [left_rank, right_rank],
        "correct_index": correct_index, "expires": time.time() + 300, "answered": False,
    }
    if len(_rarity_duel_sessions) > 2000:
        for key, val in list(_rarity_duel_sessions.items()):
            if val["expires"] < time.time() or val["answered"]:
                _rarity_duel_sessions.pop(key, None)
    public_cards = []
    for char in cards:
        public = _public_character(char)
        public_cards.append({key: public[key] for key in ("id", "name", "anime", "img_url")})
    return web.json_response({
        "question_id": question_id,
        "question": "Which character has the higher rarity?",
        "cards": public_cards,
    })


async def api_rarity_duel_answer(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    try:
        body = await request.json()
        question_id = str(body.get("question_id", ""))
        answer_index = int(body.get("answer_index"))
    except (TypeError, ValueError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Choose a character card."}), content_type="application/json")
    session = _rarity_duel_sessions.get(question_id)
    if not session or session["uid"] != tg_user["id"] or session["expires"] < time.time() or session["answered"]:
        raise web.HTTPConflict(
            text=json.dumps({"error": "This duel expired or was already answered. Start a new duel."}),
            content_type="application/json",
        )
    if answer_index not in (0, 1):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid card choice."}), content_type="application/json")
    session["answered"] = True
    correct = answer_index == session["correct_index"]
    earned = _RARITY_DUEL_REWARD if correct else 0
    xp = _RARITY_DUEL_XP if correct else 0
    if correct:
        await user_collection.update_one(
            {"id": tg_user["id"]},
            {"$inc": {"coins": earned, "xp": xp},
             "$set": {"first_name": tg_user.get("first_name", "Collector"), "username": tg_user.get("username")},
             "$setOnInsert": {"id": tg_user["id"], "characters": [], "wins": 0, "favorites": []}},
            upsert=True,
        )
    rank_lookup = _rarity_rank_lookup(await _get_admin_settings())
    rarities = [str(char.get("rarity", "") or "Common") for char in session["cards"]]
    return web.json_response({
        "correct": correct, "correct_index": session["correct_index"], "rarities": rarities,
        "coins_earned": earned, "xp_earned": xp,
    })


async def api_memory_match_start(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    chars = await _sample_game_characters(300)
    unique = {}
    for char in chars:
        char_id = str(char.get("id", "") or "").strip()
        name = str(char.get("name", "") or "").strip()
        anime = str(char.get("anime", "") or "").strip()
        if not name or not anime:
            continue
        key = char_id or (name.casefold() + "|" + anime.casefold())
        unique.setdefault(key, char)
    candidates = list(unique.values())
    if len(candidates) < 8:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Memory Match needs at least 8 different character images."}),
            content_type="application/json",
        )
    pairs = random.sample(candidates, 8)
    board = pairs * 2
    random.shuffle(board)
    session_id = hashlib.sha256(
        f"{tg_user['id']}:memory-match:{time.time_ns()}:{random.random()}".encode()
    ).hexdigest()[:24]
    _memory_match_sessions[session_id] = {
        "uid": tg_user["id"], "board": board, "revealed": set(), "matched": set(),
        "pending": [], "moves": 0, "expires": time.time() + 600, "over": False,
        "awarded": False,
    }
    if len(_memory_match_sessions) > 1000:
        for key, val in list(_memory_match_sessions.items()):
            if val["expires"] < time.time() or val["over"]:
                _memory_match_sessions.pop(key, None)
    return web.json_response({
        "game_id": session_id, "total_tiles": 16, "pairs_total": 8, "expires_in": 600,
    })


async def api_memory_match_reveal(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    try:
        body = await request.json()
        game_id = str(body.get("game_id", ""))
        tile = int(body.get("tile"))
    except (TypeError, ValueError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Choose a valid tile."}), content_type="application/json")
    session = _memory_match_sessions.get(game_id)
    if not session or session["uid"] != tg_user["id"] or session["expires"] < time.time() or session["over"]:
        raise web.HTTPConflict(
            text=json.dumps({"error": "Memory game expired. Start a new game."}),
            content_type="application/json",
        )
    if tile < 0 or tile >= 16:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid tile position."}), content_type="application/json")
    hide_indices = []
    pending = list(session["pending"])
    if len(pending) == 2:
        hide_indices = [idx for idx in pending if idx not in session["matched"]]
        session["revealed"].difference_update(hide_indices)
        session["pending"] = []
    if tile in session["matched"] or tile in session["revealed"]:
        raise web.HTTPConflict(text=json.dumps({"error": "That tile is already open. Choose another tile."}), content_type="application/json")
    session["revealed"].add(tile)
    card = session["board"][tile]
    matched = False
    matched_indices = []
    if not session["pending"]:
        session["pending"] = [tile]
    else:
        first = session["pending"][0]
        session["moves"] += 1
        first_char = session["board"][first]
        first_id = str(first_char.get("id", "") or "").strip()
        card_id = str(card.get("id", "") or "").strip()
        matched = bool(first_id and card_id and first_id == card_id)
        if not matched:
            matched = (str(first_char.get("name", "")).casefold() == str(card.get("name", "")).casefold()
                       and str(first_char.get("anime", "")).casefold() == str(card.get("anime", "")).casefold())
        matched_indices = [first, tile] if matched else []
        if matched:
            session["matched"].update(matched_indices)
            session["pending"] = []
        else:
            session["pending"] = [first, tile]
    coins_earned = 0
    xp_earned = 0
    if len(session["matched"]) == 16:
        session["over"] = True
        if not session["awarded"]:
            session["awarded"] = True
            coins_earned = _MEMORY_MATCH_REWARD
            xp_earned = _MEMORY_MATCH_XP
            await user_collection.update_one(
                {"id": tg_user["id"]},
                {"$inc": {"coins": coins_earned, "xp": xp_earned},
                 "$set": {"first_name": tg_user.get("first_name", "Collector"), "username": tg_user.get("username")},
                 "$setOnInsert": {"id": tg_user["id"], "characters": [], "wins": 0, "favorites": []}},
                upsert=True,
            )
    public_card = _public_character(card)
    return web.json_response({
        "tile": tile, "card": {key: public_card[key] for key in ("id", "name", "anime", "img_url", "rarity")},
        "hide_indices": hide_indices, "matched": matched, "matched_indices": matched_indices,
        "matched_tiles": len(session["matched"]), "matched_pairs": len(session["matched"]) // 2,
        "moves": session["moves"], "game_over": session["over"],
        "coins_earned": coins_earned, "xp_earned": xp_earned,
    })


async def api_extra_game_start(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    game = str(request.match_info.get("game", "")).strip().lower()
    if game not in {"scramble", "anime-match"}:
        raise web.HTTPNotFound(text=json.dumps({"error": "Game not found."}), content_type="application/json")

    sampled = await _sample_game_characters(300)
    question_id = hashlib.sha256(
        f"{tg_user['id']}:{game}:{time.time_ns()}:{random.random()}".encode()
    ).hexdigest()[:24]

    if game == "scramble":
        unique_names = {}
        for char in sampled:
            name = str(char.get("name", "")).strip()
            if len(_normalize_game_answer(name)) >= 4 and name.casefold() not in unique_names:
                unique_names[name.casefold()] = name
        candidates = list(unique_names.values())
        if not candidates:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "No suitable character names are available for a scramble right now."}),
                content_type="application/json",
            )
        correct = random.choice(candidates)
        letters = [char for char in correct if char.isalnum()]
        random.shuffle(letters)
        if "".join(letters).casefold() == "".join(char for char in correct if char.isalnum()).casefold():
            letters = letters[1:] + letters[:1]
        _extra_game_sessions[question_id] = {
            "uid": tg_user["id"], "game": game, "correct": correct,
            "expires": time.time() + 300, "answered": False,
        }
        if len(_extra_game_sessions) > 2000:
            for key, value in list(_extra_game_sessions.items()):
                if value.get("expires", 0) < time.time() or value.get("answered"):
                    _extra_game_sessions.pop(key, None)
        return web.json_response({
            "question_id": question_id,
            "scrambled_letters": " ".join(letters),
            "prompt": "Unscramble this character's name",
        })

    anime_map = {}
    for char in sampled:
        anime = str(char.get("anime", "")).strip()
        name = str(char.get("name", "")).strip()
        image = str(char.get("img_url", "")).strip()
        if anime and name and image:
            anime_map.setdefault(anime.casefold(), {"anime": anime, "character": char})
    candidates = list(anime_map.values())
    if len(candidates) < 4:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Not enough distinct anime titles are available for this game yet."}),
            content_type="application/json",
        )
    chosen = random.choice(candidates)
    others = [item["anime"] for item in candidates if item["anime"].casefold() != chosen["anime"].casefold()]
    options = [chosen["anime"]] + random.sample(others, 3)
    random.shuffle(options)
    correct = chosen["anime"]
    _extra_game_sessions[question_id] = {
        "uid": tg_user["id"], "game": game, "correct": correct,
        "answer_index": options.index(correct), "options": options,
        "expires": time.time() + 300, "answered": False,
    }
    if len(_extra_game_sessions) > 2000:
        for key, value in list(_extra_game_sessions.items()):
            if value.get("expires", 0) < time.time() or value.get("answered"):
                _extra_game_sessions.pop(key, None)
    public_char = _public_character(chosen["character"])
    return web.json_response({
        "question_id": question_id,
        "question": "Which anime is this character from?",
        "character_id": public_char["id"],
        "img_url": public_char["img_url"],
        "options": options,
    })


async def api_extra_game_answer(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    game = str(request.match_info.get("game", "")).strip().lower()
    if game not in {"scramble", "anime-match"}:
        raise web.HTTPNotFound(text=json.dumps({"error": "Game not found."}), content_type="application/json")
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid answer payload."}), content_type="application/json")
    question_id = str(body.get("question_id", ""))
    session = _extra_game_sessions.get(question_id)
    if (not session or session.get("game") != game or session["uid"] != tg_user["id"]
            or session["expires"] < time.time() or session["answered"]):
        raise web.HTTPConflict(
            text=json.dumps({"error": "Question expired or already answered. Start a new round."}),
            content_type="application/json",
        )
    if game == "scramble":
        answer = str(body.get("answer", ""))[:200]
        if not _normalize_game_answer(answer):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Enter your answer first."}), content_type="application/json")
        correct_answer = _normalize_game_answer(session["correct"])
        correct = _normalize_game_answer(answer) == correct_answer
    else:
        try:
            answer_index = int(body.get("answer_index"))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Choose an answer."}), content_type="application/json")
        if answer_index < 0 or answer_index >= len(session["options"]):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid answer choice."}), content_type="application/json")
        correct = answer_index == session["answer_index"]
    session["answered"] = True
    coins_earned = EXTRA_GAME_REWARD if correct else 0
    xp_earned = EXTRA_GAME_XP if correct else 0
    if correct:
        await user_collection.update_one(
            {"id": tg_user["id"]},
            {"$inc": {"coins": coins_earned, "xp": xp_earned},
             "$set": {"first_name": tg_user.get("first_name", "Collector"), "username": tg_user.get("username")},
             "$setOnInsert": {"id": tg_user["id"], "characters": [], "wins": 0, "favorites": []}},
            upsert=True,
        )
    return web.json_response({
        "correct": correct,
        "answer": session["correct"],
        "coins_earned": coins_earned,
        "xp_earned": xp_earned,
    })


async def api_quiz_start(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    all_chars = await _sample_game_characters(250)

    # Quiz only uses characters with a usable image field and distinct names.
    # img_url may be a normal URL OR a Telegram photo file_id from /upload.
    chars_by_name: dict[str, dict] = {}
    for char in all_chars:
        name = str(char.get("name", "")).strip()
        image = str(char.get("img_url", "")).strip()
        if not name or name.lower() == "unknown" or not image:
            continue
        chars_by_name.setdefault(name.casefold(), char)
    chars = list(chars_by_name.values())
    if len(chars) < 4:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "At least 4 uniquely named characters with images are needed for a quiz."}),
            content_type="application/json",
        )

    correct = random.choice(chars)
    correct_name = str(correct.get("name", "")).strip()
    pool = [c for c in chars if str(c.get("name", "")).strip().casefold() != correct_name.casefold()]
    wrong = random.sample(pool, 3)
    choices = [correct_name] + [str(c.get("name", "")).strip() for c in wrong]
    random.shuffle(choices)
    question_id = hashlib.sha256(f"{tg_user['id']}:{time.time_ns()}:{random.random()}".encode()).hexdigest()[:24]
    _quiz_sessions[question_id] = {
        "uid": tg_user["id"], "correct": correct_name,
        "answer_index": choices.index(correct_name),
        "choices": choices, "expires": time.time() + 300, "answered": False,
    }
    if len(_quiz_sessions) > 2000:
        for key, val in list(_quiz_sessions.items()):
            if val["expires"] < time.time() or val["answered"]:
                _quiz_sessions.pop(key, None)
    return web.json_response({
        "question_id": question_id, "question": "Who is this character?",
        "character_id": str(correct.get("id", "")),
        "img_url": _public_character(correct)["img_url"],
        "options": choices,
    })


async def api_character_image(request: web.Request) -> web.StreamResponse:
    """Serve Telegram character media quickly with browser and bounded memory caching."""
    global _CHARACTER_IMAGE_CACHE_BYTES
    char_id = request.match_info.get("char_id", "").strip()
    id_candidates = [{"id": char_id}]
    if char_id.isdigit():
        id_candidates.append({"id": int(char_id)})

    char = await collection.find_one(
        {"$or": id_candidates},
        {"img_url": 1, "id": 1, "message_id": 1},
    )
    if not char:
        char = await db["miniapp_store_characters"].find_one(
            {"$or": id_candidates}, {"img_url": 1, "id": 1, "message_id": 1}
        )
    if not char:
        raise web.HTTPNotFound(text="Character image not found")

    image_ref = str(char.get("img_url", "") or "").strip()
    cache_key = (char_id, f"{image_ref}|mikasaverse-watermark-v1")
    now = time.monotonic()
    async with _CHARACTER_IMAGE_CACHE_LOCK:
        cached = _CHARACTER_IMAGE_CACHE.get(cache_key)
        if cached:
            expires_at, cached_data, cached_type = cached
            if expires_at > now:
                _CHARACTER_IMAGE_CACHE.move_to_end(cache_key)
                return web.Response(
                    body=cached_data,
                    content_type=cached_type,
                    headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"},
                )
            _CHARACTER_IMAGE_CACHE.pop(cache_key, None)
            _CHARACTER_IMAGE_CACHE_BYTES -= len(cached_data)

    public_url = image_ref if image_ref.startswith(("https://", "http://")) else ""
    message_id = char.get("message_id")
    media_doc = await db["miniapp_settings"].find_one({"_id": "media"})
    media_settings = (media_doc or {}).get("data") or {}
    if image_ref and not public_url and media_settings.get("telegram_image_proxy", True) is False:
        raise web.HTTPServiceUnavailable(text="Telegram image proxy is disabled by the bot owner")
    bot = application.bot
    tg_file = None

    try:
        # Telegram file_id values work directly with this bot. Older records may
        # instead contain a URL; for those, recover the original Telegram media
        # from its saved channel post before trying the possibly expired URL.
        if image_ref and not public_url:
            try:
                tg_file = await bot.get_file(image_ref)
            except Exception as file_error:
                logger.warning(
                    "Stored Telegram image ID failed for character %s (%s)",
                    char_id, type(file_error).__name__,
                )

        if tg_file is None and message_id and media_settings.get("image_fallback", True) is not False:
            copied = None
            try:
                copied = await bot.forward_message(
                    chat_id=CHARA_CHANNEL_ID,
                    from_chat_id=CHARA_CHANNEL_ID,
                    message_id=int(message_id),
                )
                recovered = copied.photo[-1] if copied.photo else None
                if not recovered and copied.document:
                    mime = copied.document.mime_type or ""
                    if mime.startswith("image/"):
                        recovered = copied.document
                if not recovered:
                    raise RuntimeError("Source channel post has no recoverable image")

                image_ref = recovered.file_id
                await collection.update_one(
                    {"_id": char["_id"]},
                    {"$set": {"img_url": image_ref}},
                )
                tg_file = await bot.get_file(image_ref)
            except Exception as recovery_error:
                logger.warning(
                    "Could not recover character %s from channel post %s (%s)",
                    char_id, message_id, type(recovery_error).__name__,
                )
                tg_file = None
            finally:
                if copied is not None:
                    try:
                        await bot.delete_message(CHARA_CHANNEL_ID, copied.message_id)
                    except Exception:
                        logger.warning(
                            "Could not delete temporary recovery post for character %s",
                            char_id,
                        )

        # Last resort for legacy records without a usable Telegram file_id:
        # download the saved public URL so inline and Mini App images can also
        # receive the same MikasaVerse watermark.
        if tg_file is None and public_url:
            timeout = aiohttp.ClientTimeout(total=20, connect=8)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(public_url, allow_redirects=True) as response:
                        response.raise_for_status()
                        remote_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
                        if remote_type and not remote_type.startswith("image/"):
                            raise web.HTTPBadGateway(text="The character URL did not return an image")
                        data = await response.content.read(15 * 1024 * 1024 + 1)
            except web.HTTPException:
                raise
            except Exception as download_error:
                raise web.HTTPBadGateway(text="Could not download the character image") from download_error
        elif tg_file is None:
            raise web.HTTPBadGateway(text="Character image could not be recovered")
        else:
            data = bytes(await tg_file.download_as_bytearray())

        if not data:
            raise web.HTTPBadGateway(text="Character image source returned an empty image")
        if len(data) > 15 * 1024 * 1024:
            raise web.HTTPBadGateway(text="Character image exceeds the 15 MB processing limit")

        try:
            data = await asyncio.to_thread(watermark_image_bytes, data)
        except Exception as watermark_error:
            logger.exception("Could not apply MikasaVerse watermark to character %s", char_id)
            raise web.HTTPBadGateway(text="Could not watermark the character image") from watermark_error

        content_type = "image/jpeg"
        cache_key = (char_id, f"{image_ref}|mikasaverse-watermark-v1")
        if len(data) <= _CHARACTER_IMAGE_CACHE_MAX_ITEM_BYTES:
            async with _CHARACTER_IMAGE_CACHE_LOCK:
                previous = _CHARACTER_IMAGE_CACHE.pop(cache_key, None)
                if previous:
                    _CHARACTER_IMAGE_CACHE_BYTES -= len(previous[1])
                _CHARACTER_IMAGE_CACHE[cache_key] = (
                    time.monotonic() + _CHARACTER_IMAGE_CACHE_TTL, data, content_type
                )
                _CHARACTER_IMAGE_CACHE_BYTES += len(data)
                while _CHARACTER_IMAGE_CACHE and _CHARACTER_IMAGE_CACHE_BYTES > _CHARACTER_IMAGE_CACHE_MAX_BYTES:
                    _, evicted = _CHARACTER_IMAGE_CACHE.popitem(last=False)
                    _CHARACTER_IMAGE_CACHE_BYTES -= len(evicted[1])
        return web.Response(
            body=data,
            content_type=content_type,
            headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"},
        )
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.exception("Character image proxy failed for character %s", char_id)
        raise web.HTTPBadGateway(text="Could not load character image") from exc


async def api_quiz_answer(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("quiz")
    active_settings = await _get_admin_settings()
    game_settings = active_settings.get("game", {})
    body = await request.json()
    qid = str(body.get("question_id", ""))
    session = _quiz_sessions.get(qid)
    if not session or session["uid"] != tg_user["id"] or session["expires"] < time.time() or session["answered"]:
        raise web.HTTPConflict(text=json.dumps({"error": "Question expired or already answered. Start a new quiz."}), content_type="application/json")
    try:
        chosen = int(body.get("answer_index"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid answer"}), content_type="application/json")
    if chosen < 0 or chosen >= len(session["choices"]):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid answer"}), content_type="application/json")
    session["answered"] = True
    correct = chosen == session["answer_index"]
    earned = max(0, min(1000000, int(game_settings.get("quiz_reward", QUIZ_REWARD)))) if correct else 0
    if correct:
        await user_collection.update_one(
            {"id": tg_user["id"]},
            {"$inc": {"coins": earned, "xp": max(0, min(1000000, int(game_settings.get("quiz_xp_reward", QUIZ_XP))))},
             "$set": {"first_name": tg_user.get("first_name", "Collector"), "username": tg_user.get("username")},
             "$setOnInsert": {"id": tg_user["id"], "characters": [], "wins": 0, "favorites": []}},
            upsert=True,
        )
    return web.json_response({"correct": correct, "coins_earned": earned, "xp_earned": max(0, min(1000000, int(game_settings.get("quiz_xp_reward", QUIZ_XP)))) if correct else 0, "answer": session["correct"]})


def _mines_public(game: dict) -> dict:
    # Do not leak mine positions for unrevealed tiles.
    return {
        "revealed": sorted(game["revealed"]),
        "mines": sorted(game["mines"]) if game["over"] else sorted(game["mines"].intersection(game["revealed"])),
        "safe": game["safe"], "hits": game["hits"], "over": game["over"],
        "won": game["won"], "score": game["safe"],
        "hit": game.get("last_hit", False),
    }


async def api_mines_start(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("mines")
    uid = tg_user["id"]
    existing = _mines_games.get(uid)
    if existing and not existing["over"]:
        raise web.HTTPConflict(text=json.dumps({"error": "You already have an active mines game."}), content_type="application/json")
    _mines_games[uid] = {"mines": set(random.sample(range(TOTAL_TILES), MINES_COUNT)), "revealed": set(), "hits": 0, "safe": 0, "over": False, "won": False, "last_hit": False}
    return web.json_response(_mines_public(_mines_games[uid]))


async def api_mines_reveal(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("mines")
    body = await request.json()
    try:
        tile = int(body.get("tile"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid tile"}), content_type="application/json")
    if tile < 0 or tile >= TOTAL_TILES:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid tile"}), content_type="application/json")
    game = _mines_games.get(tg_user["id"])
    if not game or game["over"]:
        raise web.HTTPConflict(text=json.dumps({"error": "No active mines game."}), content_type="application/json")
    if tile in game["revealed"]:
        raise web.HTTPConflict(text=json.dumps({"error": "That tile is already revealed."}), content_type="application/json")
    game["revealed"].add(tile)
    game["last_hit"] = tile in game["mines"]
    if game["last_hit"]:
        game["hits"] += 1
        if game["hits"] >= 2:
            game["over"] = True
    else:
        game["safe"] += 1
        if game["safe"] >= TOTAL_TILES - MINES_COUNT:
            game["over"] = True
            game["won"] = True
    return web.json_response(_mines_public(game))


async def api_mines_finish(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("mines")
    game = _mines_games.get(tg_user["id"])
    if not game or game["over"]:
        raise web.HTTPConflict(text=json.dumps({"error": "No active mines puzzle."}), content_type="application/json")
    game["over"] = True
    game["won"] = True
    return web.json_response(_mines_public(game))


async def api_status(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "adamwaifus-miniapp-api"})



async def api_public_config(request: web.Request) -> web.Response:
    await _identity(request)
    settings = await _get_admin_settings()
    general = settings.get("general", {})
    game = settings.get("game", {})
    features = settings.get("features", {})
    media = settings.get("media", {})
    flags = {
        "market": features.get("market", True) and game.get("market_enabled", True),
        "quiz": features.get("quiz", True) and game.get("quiz_enabled", True),
        "mines": features.get("mines", True) and game.get("mines_enabled", True),
        "trades": features.get("trades", True) and game.get("trades_enabled", True),
        "duels": features.get("duels", True),
        "nguess": features.get("nguess", True),
        "user_settings": features.get("user_settings", True),
        "events": features.get("events", True),
    }

    # Evaluate schedules on the server using the configured timezone, so the
    # website and bot rarity picker agree no matter where viewers are located.
    try:
        event_tz = ZoneInfo(str(general.get("timezone", "Asia/Kolkata")))
    except Exception:
        event_tz = timezone.utc
    now = datetime.now(event_tz)

    def parse_event_date(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).strip())
            return parsed.replace(tzinfo=event_tz) if parsed.tzinfo is None else parsed.astimezone(event_tz)
        except (TypeError, ValueError):
            return None

    enabled_events = []
    if flags["events"]:
        for event in settings.get("events", []):
            if not isinstance(event, dict) or event.get("enabled") is not True:
                continue
            start_at = parse_event_date(event.get("start_at"))
            end_at = parse_event_date(event.get("end_at"))
            if start_at and now < start_at:
                continue
            if end_at and now > end_at:
                continue
            enabled_events.append(event)
            if len(enabled_events) >= 20:
                break
    return web.json_response({
        "announcement": str(general.get("announcement", ""))[:1000],
        "maintenance_mode": bool(general.get("maintenance_mode", False)),
        "maintenance_message": str(general.get("maintenance_message", ""))[:1000],
        "features": flags,
        "media": {"show_broken_image_alerts": media.get("show_broken_image_alerts", True)},
        "events": enabled_events,
    })


async def _run_admin_broadcast(broadcast_id: str, message: str, actor_id: int) -> None:
    store = db["admin_broadcasts"]
    delivered = 0
    failed = 0
    try:
        recipients = await db["pm_users"].find({}, {"_id": 1}).limit(5000).to_list(5000)
        await store.update_one(
            {"_id": broadcast_id},
            {"$set": {"status": "running", "recipient_count": len(recipients), "started_at": int(time.time())}},
        )
        for index, recipient in enumerate(recipients, start=1):
            chat_id = recipient.get("_id")
            try:
                await application.bot.send_message(chat_id=int(chat_id), text=message)
                delivered += 1
            except Exception as exc:
                retry_after = getattr(exc, "retry_after", 0)
                if retry_after:
                    await asyncio.sleep(min(60, max(1, float(retry_after))))
                    try:
                        await application.bot.send_message(chat_id=int(chat_id), text=message)
                        delivered += 1
                    except Exception:
                        failed += 1
                else:
                    failed += 1
            if index % 20 == 0:
                await store.update_one(
                    {"_id": broadcast_id},
                    {"$set": {"delivered": delivered, "failed": failed, "processed": index, "updated_at": int(time.time())}},
                )
            await asyncio.sleep(0.05)
        await store.update_one(
            {"_id": broadcast_id},
            {"$set": {
                "status": "completed", "delivered": delivered, "failed": failed,
                "processed": len(recipients), "finished_at": int(time.time()),
            }},
        )
        await _write_admin_audit(
            {"id": actor_id, "first_name": "Owner"}, "broadcast.completed", broadcast_id,
            {"recipient_count": len(recipients), "delivered": delivered, "failed": failed},
        )
    except Exception as exc:
        logger.exception("Owner broadcast %s failed: %s", broadcast_id, type(exc).__name__)
        await store.update_one(
            {"_id": broadcast_id},
            {"$set": {"status": "failed", "delivered": delivered, "failed": failed, "error": "Broadcast interrupted; check logs.", "finished_at": int(time.time())}},
        )


async def api_admin_broadcasts(request: web.Request) -> web.Response:
    actor = await _admin_identity(request)
    store = db["admin_broadcasts"]
    if request.method == "GET":
        rows = await store.find(
            {}, {"_id": 1, "message": 1, "status": 1, "recipient_count": 1,
                 "delivered": 1, "failed": 1, "processed": 1, "created_at": 1,
                 "started_at": 1, "finished_at": 1, "error": 1}
        ).sort("created_at", -1).limit(20).to_list(20)
        return web.json_response({"broadcasts": [{
            "id": str(row.get("_id", "")), "message": str(row.get("message", "")),
            "status": str(row.get("status", "unknown")), "recipient_count": int(row.get("recipient_count", 0) or 0),
            "delivered": int(row.get("delivered", 0) or 0), "failed": int(row.get("failed", 0) or 0),
            "processed": int(row.get("processed", 0) or 0), "created_at": int(row.get("created_at", 0) or 0),
            "started_at": int(row.get("started_at", 0) or 0), "finished_at": int(row.get("finished_at", 0) or 0),
            "error": str(row.get("error", "")),
        } for row in rows]})
    settings = await _get_admin_settings()
    if settings.get("features", {}).get("broadcasts", True) is False:
        raise web.HTTPServiceUnavailable(text=json.dumps({"error": "Broadcasts are disabled in Feature toggles."}), content_type="application/json")
    try:
        body = await request.json()
        message = str(body.get("message", "")).strip()
    except (ValueError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Provide a valid broadcast message."}), content_type="application/json")
    if not message or len(message) > 3000:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Broadcast message must contain 1–3000 characters."}), content_type="application/json")
    running = await store.find_one({"status": {"$in": ["queued", "running"]}}, {"_id": 1})
    if running:
        raise web.HTTPConflict(text=json.dumps({"error": "Another broadcast is already running."}), content_type="application/json")
    broadcast_id = uuid.uuid4().hex
    await store.insert_one({
        "_id": broadcast_id, "message": message, "status": "queued",
        "actor_id": int(actor["id"]), "created_at": int(time.time()),
        "recipient_count": 0, "delivered": 0, "failed": 0, "processed": 0,
    })
    asyncio.create_task(_run_admin_broadcast(broadcast_id, message, int(actor["id"])))
    await _write_admin_audit(actor, "broadcast.started", broadcast_id, {"message_length": len(message)})
    return web.json_response({"ok": True, "broadcast_id": broadcast_id, "status": "queued"})


async def api_admin_export(request: web.Request) -> web.Response:
    await _admin_identity(request)
    async def rows_for(cursor, limit: int) -> list:
        rows = await cursor.limit(limit + 1).to_list(limit + 1)
        truncated = len(rows) > limit
        rows = rows[:limit]
        return rows, truncated

    users, users_truncated = await rows_for(
        user_collection.find({}, {"_id": 0, "id": 1, "username": 1, "first_name": 1,
                                  "coins": 1, "xp": 1, "wins": 1, "characters": 1,
                                  "miniapp_suspended": 1}), 5000)
    chars, chars_truncated = await rows_for(
        collection.find({}, {"_id": 0, "id": 1, "name": 1, "anime": 1, "rarity": 1, "img_url": 1}), 5000)
    listings, listings_truncated = await rows_for(
        market_collection.find({}, {"_id": 0, "seller_id": 1, "seller_name": 1,
                                    "price": 1, "char": 1}), 5000)
    settings = await _get_admin_settings()
    audit = await db["admin_audit_log"].find(
        {}, {"_id": 0, "actor_id": 1, "actor_name": 1, "action": 1, "target": 1, "details": 1, "created_at": 1}
    ).sort("created_at", -1).limit(500).to_list(500)
    # Convert nested Mongo documents to a JSON-safe export (embedded characters can
    # carry ObjectIds even when the top-level collection projection excludes _id).
    users = [{
        **{key: row.get(key) for key in ("id", "username", "first_name", "coins", "xp", "wins", "miniapp_suspended")},
        "characters": [{
            "id": str(char.get("id", "")), "name": str(char.get("name", "Unknown")),
            "anime": str(char.get("anime", "Unknown")), "rarity": str(char.get("rarity", "Character")),
            "img_url": str(char.get("img_url", "") or ""),
        } for char in (row.get("characters", []) or []) if isinstance(char, dict)][:5000],
    } for row in users]
    listings = [{
        "listing_id": str(row.get("_id", "")),
        "seller_id": int(row.get("seller_id", 0) or 0),
        "seller_name": str(row.get("seller_name", "Collector")),
        "price": max(0, int(row.get("price", 0) or 0)),
        "char": {
            "id": str((row.get("char") or {}).get("id", "")),
            "name": str((row.get("char") or {}).get("name", "Unknown")),
            "anime": str((row.get("char") or {}).get("anime", "Unknown")),
            "rarity": str((row.get("char") or {}).get("rarity", "Character")),
            "img_url": str((row.get("char") or {}).get("img_url", "") or ""),
        },
    } for row in listings]
    redeem_codes, redeem_truncated = await rows_for(
        db["redeem_codes"].find({}, {"_id": 0, "code": 1, "type": 1, "amount": 1,
                                     "char_id": 1, "char_name": 1, "char_rarity": 1,
                                     "uses_left": 1, "total_uses": 1, "used_by": 1}), 1000)
    await _write_admin_audit(
        await _admin_identity(request), "backup.export", "owner-export",
        {"users": len(users), "characters": len(chars), "market_listings": len(listings), "redeem_codes": len(redeem_codes)},
    )
    return web.json_response({
        "format": "grey-waifu-owner-export-v1", "created_at": int(time.time()),
        "limits": {"users": 5000, "characters": 5000, "market_listings": 5000, "redeem_codes": 1000, "audit": 500},
        "truncated": {"users": users_truncated, "characters": chars_truncated, "market_listings": listings_truncated, "redeem_codes": redeem_truncated},
        "users": users, "characters": chars, "market_listings": listings,
        "redeem_codes": redeem_codes, "settings": settings, "audit_log": audit,
    })


async def api_admin_redeem_codes(request: web.Request) -> web.Response:
    actor = await _admin_identity(request)
    store = db["redeem_codes"]
    if request.method == "GET":
        rows = await store.find(
            {}, {"_id": 0, "code": 1, "type": 1, "amount": 1, "char_id": 1,
                 "char_name": 1, "char_rarity": 1, "uses_left": 1, "total_uses": 1}
        ).sort("code", 1).limit(200).to_list(200)
        return web.json_response({"codes": rows})

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid redeem-code request."}), content_type="application/json")

    if request.method == "DELETE":
        code = str(body.get("code", "")).strip().upper()
        if not code:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Redeem code is required."}), content_type="application/json")
        result = await store.delete_one({"code": code})
        if not result.deleted_count:
            raise web.HTTPNotFound(text=json.dumps({"error": "Redeem code not found."}), content_type="application/json")
        await _write_admin_audit(actor, "redeem.delete", code, {})
        return web.json_response({"ok": True, "code": code})

    try:
        code_type = str(body.get("type", "")).strip().lower()
        uses = int(body.get("uses", 1))
        economy_doc = await db["miniapp_settings"].find_one({"_id": "economy"})
        economy = (economy_doc or {}).get("data") or {}
        max_uses = max(1, min(100000, int(economy.get("redeem_code_max_claims", 100))))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid amount or number of uses."}), content_type="application/json")
    if uses < 1 or uses > max_uses:
        raise web.HTTPBadRequest(text=json.dumps({"error": f"Uses must be between 1 and {max_uses:,}."}), content_type="application/json")

    code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=10))
    for _ in range(5):
        if not await store.find_one({"code": code}, {"_id": 1}):
            break
        code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=10))
    if await store.find_one({"code": code}, {"_id": 1}):
        raise web.HTTPConflict(text=json.dumps({"error": "Could not create a unique code. Try again."}), content_type="application/json")

    if code_type == "coins":
        try:
            amount = int(body.get("amount", 0))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Coin reward must be a whole number."}), content_type="application/json")
        if amount < 1 or amount > 100000000:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Coin reward must be between 1 and 100,000,000."}), content_type="application/json")
        doc = {"code": code, "type": "coins", "amount": amount, "uses_left": uses, "total_uses": uses, "used_by": []}
        summary = {"type": "coins", "amount": amount, "uses": uses}
    elif code_type == "char":
        char_id = str(body.get("char_id", "")).strip()
        candidates = [{"id": char_id}]
        if char_id.isdigit():
            candidates.append({"id": int(char_id)})
        char = await collection.find_one({"$or": candidates})
        if not char:
            raise web.HTTPNotFound(text=json.dumps({"error": "Character not found."}), content_type="application/json")
        doc = {
            "code": code, "type": "char", "char_id": char_id,
            "char_name": str(char.get("name", "Character")),
            "char_rarity": str(char.get("rarity", "Unknown")),
            "uses_left": uses, "total_uses": uses, "used_by": [],
        }
        summary = {"type": "char", "char_id": char_id, "char_name": doc["char_name"], "uses": uses}
    else:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Code type must be coins or char."}), content_type="application/json")

    await store.insert_one(doc)
    await _write_admin_audit(actor, "redeem.create", code, summary)
    return web.json_response({"ok": True, "code": code, "details": summary}, status=201)


async def api_admin_restore(request: web.Request) -> web.Response:
    actor = await _admin_identity(request)
    try:
        body = await request.json()
        backup = body.get("backup")
        sections = set(body.get("sections") or [])
        confirmation = str(body.get("confirmation", ""))
        allow_truncated = body.get("allow_truncated") is True
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid restore request."}), content_type="application/json")
    if not isinstance(backup, dict) or backup.get("format") != "grey-waifu-owner-export-v1":
        raise web.HTTPBadRequest(text=json.dumps({"error": "Select a valid Grey Waifu JSON export first."}), content_type="application/json")
    if confirmation != "RESTORE MERGE":
        raise web.HTTPBadRequest(text=json.dumps({"error": "Type RESTORE MERGE to confirm the restore."}), content_type="application/json")
    allowed = {"users", "characters", "settings", "redeem_codes", "market_listings"}
    if not sections or not sections.issubset(allowed):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Choose one or more supported restore sections."}), content_type="application/json")
    truncated = backup.get("truncated") or {}
    for section in sections:
        if section in ("users", "characters", "redeem_codes", "market_listings") and truncated.get(section) and not allow_truncated:
            raise web.HTTPBadRequest(text=json.dumps({"error": f"The {section} section is truncated. Confirm partial merge explicitly to continue."}), content_type="application/json")
    counts = {}
    if "users" in sections:
        rows = backup.get("users")
        if not isinstance(rows, list) or len(rows) > 5000:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Backup must contain at most 5,000 user records."}), content_type="application/json")
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                uid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            if uid <= 0:
                continue
            doc = {"username": str(row.get("username") or "")[:100],
                   "first_name": str(row.get("first_name") or "Collector")[:100],
                   "coins": max(0, min(1000000000, int(row.get("coins", 0) or 0))),
                   "xp": max(0, min(1000000000, int(row.get("xp", 0) or 0))),
                   "wins": max(0, min(1000000000, int(row.get("wins", 0) or 0))),
                   "characters": row.get("characters", [])[:5000] if isinstance(row.get("characters"), list) else [],
                   "miniapp_suspended": row.get("miniapp_suspended") is True}
            await user_collection.update_one({"id": uid}, {"$set": doc, "$setOnInsert": {"id": uid, "favorites": []}}, upsert=True)
            count += 1
        counts["users"] = count

    if "characters" in sections:
        rows = backup.get("characters")
        if not isinstance(rows, list) or len(rows) > 5000:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Backup must contain at most 5,000 characters."}), content_type="application/json")
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            char_id = str(row.get("id", "")).strip()[:32]
            name = str(row.get("name", "")).strip()[:100]
            anime = str(row.get("anime", "")).strip()[:100]
            if not char_id or not name or not anime:
                continue
            data = {"name": name, "anime": anime,
                    "rarity": str(row.get("rarity", "⚪ Common")).strip()[:80],
                    "img_url": str(row.get("img_url", "")).strip()[:2000]}
            query = {"id": char_id}
            if char_id.isdigit():
                query = {"$or": [{"id": char_id}, {"id": int(char_id)}]}
            await collection.update_one(query, {"$set": data, "$setOnInsert": {"id": char_id}}, upsert=True)
            count += 1
        counts["characters"] = count

    if "settings" in sections:
        backup_settings = backup.get("settings")
        if not isinstance(backup_settings, dict):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Backup settings section is invalid."}), content_type="application/json")
        for category in _DEFAULT_ADMIN_SETTINGS:
            if category not in backup_settings:
                continue
            clean = _sanitize_settings(category, backup_settings[category])
            await db["miniapp_settings"].update_one(
                {"_id": category}, {"$set": {"data": clean, "updated_at": int(time.time()), "updated_by": actor["id"]}},
                upsert=True,
            )
            if category == "general":
                await db["bot_settings"].update_one(
                    {"_id": "maintenance"},
                    {"$set": {"enabled": bool(clean.get("maintenance_mode", False)), "updated_at": int(time.time())}},
                    upsert=True,
                )
        counts["settings"] = sum(1 for category in _DEFAULT_ADMIN_SETTINGS if category in backup_settings)

    if "market_listings" in sections:
        rows = backup.get("market_listings")
        if not isinstance(rows, list) or len(rows) > 5000:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Backup must contain at most 5,000 marketplace listings."}), content_type="application/json")
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                seller_id = int(row.get("seller_id"))
                price = int(row.get("price", 0) or 0)
            except (TypeError, ValueError):
                continue
            if seller_id <= 0 or price < 0 or price > 1000000000:
                continue
            char = row.get("char")
            if not isinstance(char, dict) or not str(char.get("id", "")).strip():
                continue
            char_doc = {
                "id": str(char.get("id", ""))[:32],
                "name": str(char.get("name", "Unknown"))[:100],
                "anime": str(char.get("anime", "Unknown"))[:100],
                "rarity": str(char.get("rarity", "Character"))[:80],
                "img_url": str(char.get("img_url", "") or "")[:2000],
            }
            listing_doc = {
                "seller_id": seller_id,
                "seller_name": str(row.get("seller_name", "Collector"))[:100],
                "price": price,
                "char": char_doc,
            }
            listing_id = str(row.get("listing_id", "")).strip()
            if ObjectId.is_valid(listing_id):
                await market_collection.update_one(
                    {"_id": ObjectId(listing_id)}, {"$set": listing_doc}, upsert=True,
                )
            else:
                await market_collection.update_one(
                    {"seller_id": seller_id, "char.id": char_doc["id"]},
                    {"$set": listing_doc}, upsert=True,
                )
            count += 1
        counts["market_listings"] = count

    if "redeem_codes" in sections:
        rows = backup.get("redeem_codes")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Backup must contain at most 1,000 redeem codes."}), content_type="application/json")
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code", "")).strip().upper()[:40]
            code_type = row.get("type")
            if not code or code_type not in ("coins", "char"):
                continue
            try:
                uses_left = max(0, min(100000, int(row.get("uses_left", 0) or 0)))
                total_uses = max(uses_left, min(100000, int(row.get("total_uses", uses_left) or uses_left)))
            except (TypeError, ValueError):
                continue
            doc = {"code": code, "type": code_type, "uses_left": uses_left,
                   "total_uses": total_uses,
                   "used_by": row.get("used_by", [])[:5000] if isinstance(row.get("used_by"), list) else []}
            if code_type == "coins":
                doc["amount"] = max(1, min(100000000, int(row.get("amount", 1) or 1)))
            else:
                doc["char_id"] = str(row.get("char_id", ""))[:32]
                doc["char_name"] = str(row.get("char_name", "Character"))[:100]
                doc["char_rarity"] = str(row.get("char_rarity", "Unknown"))[:80]
            await db["redeem_codes"].update_one({"code": code}, {"$set": doc}, upsert=True)
            count += 1
        counts["redeem_codes"] = count

    await _write_admin_audit(actor, "restore.merge", ",".join(sorted(sections)), {"counts": counts, "allow_truncated": allow_truncated})
    return web.json_response({"ok": True, "mode": "merge", "restored": counts})


async def api_admin_bulk_user_action(request: web.Request) -> web.Response:
    actor = await _admin_identity(request)
    try:
        body = await request.json()
        ids = list(dict.fromkeys(int(uid) for uid in body.get("user_ids", [])))
        action = str(body.get("action", "")).strip()
        amount = int(body.get("amount", 0))
        reason = str(body.get("reason", "")).strip()[:300]
    except (TypeError, ValueError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Provide valid user IDs and an adjustment."}), content_type="application/json")
    ids = [uid for uid in ids if uid > 0 and uid != actor["id"]][:200]
    if not ids or not reason or action not in ("coins", "xp") or amount == 0:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Choose users, a coins/XP adjustment, and a reason."}), content_type="application/json")
    security_doc = await db["miniapp_settings"].find_one({"_id": "security"})
    security = (security_doc or {}).get("data") or {}
    try:
        max_adjustment = max(1, min(1000000, int(security.get("max_bulk_adjustment", 1000000))))
    except (TypeError, ValueError):
        max_adjustment = 1000000
    if abs(amount) > max_adjustment:
        raise web.HTTPBadRequest(text=json.dumps({"error": f"Adjustment must not exceed {max_adjustment:,} per user."}), content_type="application/json")
    field = "coins" if action == "coins" else "xp"
    changed = 0
    skipped = []
    for uid in ids:
        filt = {"id": uid}
        if amount < 0:
            filt[field] = {"$gte": abs(amount)}
        result = await user_collection.update_one(filt, {"$inc": {field: amount}})
        if result.modified_count:
            changed += 1
        else:
            skipped.append(uid)
    await _write_admin_audit(actor, f"user.bulk_{action}", ",".join(map(str, ids)), {"amount": amount, "reason": reason, "changed": changed, "skipped": skipped[:50]})
    return web.json_response({"ok": True, "changed": changed, "skipped": len(skipped), "total": len(ids)})


async def _admin_identity(request: web.Request) -> dict:
    """Require fresh Telegram-signed initData belonging to the configured owner."""
    tg_user = await _identity(request)
    try:
        owner_id = int(os.environ.get("OWNER_ID", "0"))
    except (TypeError, ValueError):
        owner_id = 0
    if not owner_id or tg_user.get("id") != owner_id:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Owner access only."}),
            content_type="application/json",
        )
    return tg_user


async def _character_admin_identity(request: web.Request) -> dict:
    """Allow the owner and configured sudo IDs to manage characters only."""
    tg_user = await _identity(request)
    try:
        owner_id = int(os.environ.get("OWNER_ID", "0"))
    except (TypeError, ValueError):
        owner_id = 0
    try:
        from waifu import sudo_users
        admin_ids = {int(value) for value in sudo_users}
    except Exception:
        admin_ids = set()
    if not owner_id or (int(tg_user.get("id", 0)) != owner_id and int(tg_user.get("id", 0)) not in admin_ids):
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Owner/Admin character access only."}),
            content_type="application/json",
        )
    return tg_user


async def api_admin_overview(request: web.Request) -> web.Response:
    await _admin_identity(request)
    total_users = await user_collection.count_documents({})
    total_characters = await collection.count_documents({})
    active_users_7d = await user_collection.count_documents({"last_miniapp_active": {"$gte": int(time.time()) - 7 * 86400}})
    total_listings = await market_collection.count_documents({})
    coin_rows = await user_collection.aggregate([
        {"$group": {"_id": None, "coins": {"$sum": {"$ifNull": ["$coins", 0]}}}}
    ]).to_list(length=1)
    total_coins = int((coin_rows[0] if coin_rows else {}).get("coins", 0) or 0)
    users = await user_collection.find(
        {}, {"_id": 0, "id": 1, "username": 1, "first_name": 1, "coins": 1, "xp": 1, "characters": 1}
    ).sort("coins", -1).limit(50).to_list(50)
    public_users = [{
        "id": int(u.get("id", 0) or 0),
        "first_name": str(u.get("first_name", "Collector")),
        "username": str(u.get("username") or ""),
        "coins": int(u.get("coins", 0) or 0),
        "xp": int(u.get("xp", 0) or 0),
        "character_count": len(u.get("characters", []) or []),
    } for u in users]
    chars = await collection.find(
        {}, {"_id": 0, "id": 1, "name": 1, "anime": 1, "rarity": 1, "img_url": 1}
    ).sort("_id", -1).limit(30).to_list(30)
    return web.json_response({
        "stats": {
            "users": total_users, "active_users_7d": active_users_7d, "characters": total_characters,
            "listings": total_listings, "coins": total_coins,
        },
        "users": public_users,
        "characters": [_public_character(c) for c in chars],
    })


async def api_admin_users(request: web.Request) -> web.Response:
    await _admin_identity(request)
    query = request.query.get("q", "").strip()
    filt = {}
    if query:
        if query.isdigit():
            filt = {"id": int(query)}
        else:
            safe_query = query.replace("\\", "\\\\").replace(".", "\\.").replace("$", "\\$")
            filt = {"$or": [
                {"first_name": {"$regex": safe_query, "$options": "i"}},
                {"username": {"$regex": safe_query.lstrip("@"), "$options": "i"}},
            ]}
    users = await user_collection.find(
        filt, {"_id": 0, "id": 1, "username": 1, "first_name": 1, "coins": 1, "xp": 1,
               "characters": 1, "miniapp_suspended": 1}
    ).sort("coins", -1).limit(200).to_list(200)
    return web.json_response({"users": [{
        "id": int(u.get("id", 0) or 0),
        "first_name": str(u.get("first_name", "Collector")),
        "username": str(u.get("username") or ""),
        "coins": int(u.get("coins", 0) or 0),
        "xp": int(u.get("xp", 0) or 0),
        "character_count": len(u.get("characters", []) or []),
        "miniapp_suspended": bool(u.get("miniapp_suspended", False)),
    } for u in users]})


def _admin_character(char: dict) -> dict:
    """Owner-only character shape includes editable raw image reference."""
    result = _public_character(char)
    result["image_input"] = str(char.get("img_url", "") or "").strip()
    result["message_id"] = char.get("message_id")
    return result


async def api_admin_characters(request: web.Request) -> web.Response:
    await _character_admin_identity(request)
    query = request.query.get("q", "").strip()
    filt = {}
    if query:
        if query.isdigit():
            filt = {"$or": [{"id": query}, {"id": int(query)}]}
        else:
            safe_query = query.replace("\\", "\\\\").replace(".", "\\.").replace("$", "\\$")
            filt = {"$or": [
                {"name": {"$regex": safe_query, "$options": "i"}},
                {"anime": {"$regex": safe_query, "$options": "i"}},
            ]}
    chars = await collection.find(
        filt, {"_id": 0, "id": 1, "name": 1, "anime": 1, "rarity": 1, "img_url": 1}
    ).sort("_id", -1).limit(60).to_list(60)
    return web.json_response({"characters": [_admin_character(c) for c in chars]})


async def api_admin_adjust_coins(request: web.Request) -> web.Response:
    actor = await _admin_identity(request)
    try:
        body = await request.json()
        uid = int(body.get("user_id"))
        delta = int(body.get("delta"))
        reason = str(body.get("reason", "")).strip()[:300]
    except (TypeError, ValueError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Provide a numeric user_id, delta and reason."}), content_type="application/json")
    if uid <= 0 or uid == actor["id"] or delta == 0 or not reason:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Target must be another user, adjustment non-zero, and reason required."}), content_type="application/json")
    security_doc = await db["miniapp_settings"].find_one({"_id": "security"})
    security = (security_doc or {}).get("data") or {}
    try:
        max_adjustment = max(1, min(1000000, int(security.get("max_bulk_adjustment", 1000000))))
    except (TypeError, ValueError):
        max_adjustment = 1000000
    if abs(delta) > max_adjustment:
        raise web.HTTPBadRequest(text=json.dumps({"error": f"Adjustment may not exceed {max_adjustment:,}."}), content_type="application/json")
    filt = {"id": uid}
    if delta < 0:
        filt["coins"] = {"$gte": abs(delta)}
    result = await user_collection.update_one(filt, {"$inc": {"coins": delta}})
    if not result.modified_count:
        exists = await user_collection.find_one({"id": uid}, {"_id": 1})
        if not exists:
            raise web.HTTPNotFound(text=json.dumps({"error": "User not found."}), content_type="application/json")
        raise web.HTTPBadRequest(text=json.dumps({"error": "User does not have enough 🟡 Neko Coins for that deduction."}), content_type="application/json")
    await _write_admin_audit(actor, "user.coins", str(uid), {"delta": delta, "reason": reason})
    return web.json_response({"ok": True, "user_id": uid, "delta": delta})



# Persisted Owner Panel configuration. Only these whitelisted keys can be changed
# from the Mini App; no secrets or arbitrary MongoDB fields are exposed.
_DEFAULT_RARITIES = [
    {"emoji":"🌫️","name":"Ordinary","weight":100000,"enabled":True,"kind":"main"},
    {"emoji":"🍀","name":"Superior","weight":50000,"enabled":True,"kind":"main"},
    {"emoji":"🔮","name":"Prime","weight":25000,"enabled":True,"kind":"main"},
    {"emoji":"🟣","name":"Epic","weight":10000,"enabled":True,"kind":"main"},
    {"emoji":"🟡","name":"Legendary","weight":4000,"enabled":True,"kind":"main"},
    {"emoji":"🌟","name":"Mythic","weight":180,"enabled":True,"kind":"main"},
    {"emoji":"💠","name":"Celestial","weight":220,"enabled":True,"kind":"main"},
    {"emoji":"💮","name":"Special Edition","weight":280,"enabled":True,"kind":"main"},
    {"emoji":"🪽","name":"Limited Edition","weight":100,"enabled":True,"kind":"main"},
    {"emoji":"🔞","name":"Extreme","weight":10,"enabled":True,"kind":"main"},
    {"emoji":"❄️","name":"Winter","weight":1000,"enabled":True,"kind":"event"},
    {"emoji":"☀️","name":"Summer","weight":800,"enabled":True,"kind":"event"},
    {"emoji":"👑","name":"Elite","weight":700,"enabled":True,"kind":"event"},
    {"emoji":"🎭","name":"Cosplay","weight":600,"enabled":True,"kind":"event"},
    {"emoji":"🎃","name":"Halloween","weight":500,"enabled":True,"kind":"event"},
    {"emoji":"💝","name":"Valentine's","weight":400,"enabled":True,"kind":"event"},
]
_DEFAULT_ADMIN_SETTINGS = {
    "general": {
        "maintenance_mode": False,
        "maintenance_message": "Grey Waifu Mini App is under maintenance. Please try again soon.",
        "default_language": "en",
        "timezone": "Asia/Kolkata",
        "announcement": "",
    },
    "game": {
        "drops_enabled": True, "drop_cooldown_seconds": 60, "drop_expiry_seconds": 300,
        "claim_timeout_seconds": 60, "quiz_enabled": True, "quiz_reward": 80,
        "quiz_xp_reward": 15, "mines_enabled": True, "market_enabled": True,
        "trades_enabled": True, "daily_reward": 200, "daily_cooldown_seconds": 86400,
        "guess_xp_reward": 50, "duel_win_reward": 150, "duel_loss_reward": 30,
        "bot_quiz_easy_timeout": 40, "bot_quiz_easy_coins": 80, "bot_quiz_easy_xp": 15,
        "bot_quiz_medium_timeout": 30, "bot_quiz_medium_coins": 150, "bot_quiz_medium_xp": 25,
        "bot_quiz_hard_timeout": 20, "bot_quiz_hard_coins": 250, "bot_quiz_hard_xp": 40,
        "nguess_coins_reward": 50, "nguess_xp_reward": 25,
        "bot_mines_entry_fee": 500, "bot_mines_multiplier_1_4_percent": 120,
        "bot_mines_multiplier_5_8_percent": 150, "bot_mines_multiplier_9_11_percent": 200,
        "bot_mines_multiplier_12_13_percent": 300,
    },
    "economy": {
        "starting_coins": 0, "market_fee_percent": 0, "max_transfer_coins": 100000,
        "redeem_code_max_claims": 100, "level_xp_base": 200,
    },
    "features": {
        "market": True, "quiz": True, "mines": True, "trades": True, "duels": True, "nguess": True,
        "broadcasts": True, "user_settings": True, "events": True,
    },
    "security": {
        "rate_limit_enabled": True, "sensitive_action_confirmation": True,
        "audit_log_enabled": True, "max_bulk_adjustment": 1000000,
    },
    "media": {
        "telegram_image_proxy": True, "image_fallback": True,
        "max_image_upload_mb": 10, "show_broken_image_alerts": True,
    },
    "groups": {
        "drops_enabled": True, "allowed_group_ids": "", "blocked_group_ids": "",
        "default_drop_cooldown_seconds": 0, "group_drop_cooldowns": "",
    },
    "rarities": _DEFAULT_RARITIES,
    "events": [],
}
_USER_PREFERENCE_DEFAULTS = {
    "theme": "system", "accent": "lavender", "language": "en",
    "notifications": True, "market_alerts": True, "event_reminders": True,
    "compact_cards": False, "animations": True, "confirm_purchases": True,
    "profile_visibility": "public", "default_sort": "rarity",
}


def _merged_settings_value(category: str, stored: dict | None) -> dict | list:
    default = _DEFAULT_ADMIN_SETTINGS[category]
    if not isinstance(stored, dict):
        return default
    value = stored.get("data")
    if category in ("rarities", "events"):
        if not isinstance(value, list):
            return default
        if category == "rarities":
            stored_by_name = {
                str(item.get("name", "")).strip().casefold(): item
                for item in value
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
            if "christmas" in stored_by_name and "elite" not in stored_by_name:
                stored_by_name["elite"] = stored_by_name["christmas"]

            expected_names = {item["name"].casefold() for item in _DEFAULT_RARITIES}
            legacy_schema = not expected_names.issubset(stored_by_name)

            normalized = []
            for default_item in _DEFAULT_RARITIES:
                previous = stored_by_name.get(default_item["name"].casefold(), {})
                item = dict(default_item)
                # When upgrading an old rarity schema, reset outdated weights
                # to the new MikasaVerse balance while preserving direct-name
                # enable/disable choices. Once migrated, Owner Panel weights
                # remain fully customizable.
                if not legacy_schema:
                    try:
                        item["weight"] = max(0, min(100000, int(previous.get("weight", item["weight"]))))
                    except (TypeError, ValueError):
                        pass
                item["enabled"] = previous.get("enabled", item["enabled"]) is not False
                normalized.append(item)
            return normalized
        return copy.deepcopy(value)
    if not isinstance(value, dict):
        return default
    result = dict(default)
    result.update({key: val for key, val in value.items() if key in default})
    return result


def _invalidate_admin_settings_cache() -> None:
    global _ADMIN_SETTINGS_CACHE, _ADMIN_SETTINGS_CACHE_AT
    _ADMIN_SETTINGS_CACHE = None
    _ADMIN_SETTINGS_CACHE_AT = 0.0


async def _get_admin_settings() -> dict:
    """Load settings in one batch and reuse them briefly between rapid user requests."""
    global _ADMIN_SETTINGS_CACHE, _ADMIN_SETTINGS_CACHE_AT
    now = time.monotonic()
    if _ADMIN_SETTINGS_CACHE is not None and now - _ADMIN_SETTINGS_CACHE_AT < _ADMIN_SETTINGS_CACHE_TTL:
        return copy.deepcopy(_ADMIN_SETTINGS_CACHE)

    async with _ADMIN_SETTINGS_CACHE_LOCK:
        now = time.monotonic()
        if _ADMIN_SETTINGS_CACHE is not None and now - _ADMIN_SETTINGS_CACHE_AT < _ADMIN_SETTINGS_CACHE_TTL:
            return copy.deepcopy(_ADMIN_SETTINGS_CACHE)
        store = db["miniapp_settings"]
        categories = list(_DEFAULT_ADMIN_SETTINGS)
        rows = await store.find({"_id": {"$in": categories}}).to_list(length=len(categories))
        stored = {str(row.get("_id")): row for row in rows}
        result = {
            category: _merged_settings_value(category, stored[category]) if category in stored else copy.deepcopy(default)
            for category, default in _DEFAULT_ADMIN_SETTINGS.items()
        }
        _ADMIN_SETTINGS_CACHE = copy.deepcopy(result)
        _ADMIN_SETTINGS_CACHE_AT = time.monotonic()
        return copy.deepcopy(result)


def _sanitize_settings(category: str, incoming):
    if category not in _DEFAULT_ADMIN_SETTINGS:
        raise ValueError("Unknown settings category.")
    defaults = _DEFAULT_ADMIN_SETTINGS[category]
    if category == "rarities":
        if not isinstance(incoming, list) or len(incoming) > 50:
            raise ValueError("Rarity settings must be a list of up to 50 entries.")
        cleaned = []
        names = set()
        for item in incoming:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:60]
            emoji = str(item.get("emoji", "✦")).strip()[:12]
            if not name or name.casefold() in names:
                continue
            names.add(name.casefold())
            try:
                weight = max(0, min(100000, int(item.get("weight", 1))))
            except (TypeError, ValueError):
                weight = 1
            cleaned.append({
                "emoji": emoji or "✦", "name": name, "weight": weight,
                "enabled": item.get("enabled") is True,
                "kind": "event" if item.get("kind") == "event" else "main",
            })
        if not cleaned:
            raise ValueError("Keep at least one valid rarity.")
        if not any(row["enabled"] and row["weight"] > 0 for row in cleaned):
            raise ValueError("At least one rarity must be enabled with a weight above zero.")
        return cleaned
    if category == "events":
        if not isinstance(incoming, list) or len(incoming) > 50:
            raise ValueError("Events must be a list of up to 50 entries.")
        cleaned = []
        for item in incoming:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:80]
            if not name:
                continue
            cleaned.append({
                "name": name,
                "rarity": str(item.get("rarity", "")).strip()[:60],
                "start_at": str(item.get("start_at", "")).strip()[:40],
                "end_at": str(item.get("end_at", "")).strip()[:40],
                "enabled": item.get("enabled") is True,
                "reward": max(0, min(100000000, int(item.get("reward", 0) or 0))),
                "notes": str(item.get("notes", "")).strip()[:500],
            })
        return cleaned
    if not isinstance(incoming, dict):
        raise ValueError("Settings values must be an object.")
    clean = {}
    for key, default in defaults.items():
        if key not in incoming:
            clean[key] = default
            continue
        value = incoming[key]
        if isinstance(default, bool):
            clean[key] = value is True
        elif isinstance(default, int):
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a whole number.")
            if key.startswith("bot_mines_multiplier_"):
                number = max(0, min(1000, number))
            elif key == "daily_cooldown_seconds":
                number = max(60, min(604800, number))
            elif key.startswith("bot_quiz_") and key.endswith("_timeout"):
                number = max(5, min(300, number))
            elif "percent" in key:
                number = max(0, min(100, number))
            elif "seconds" in key:
                number = max(0, min(86400, number))
            elif "mb" in key:
                number = max(1, min(24, number))
            elif key == "max_bulk_adjustment":
                number = max(1, min(1000000, number))
            elif key == "max_transfer_coins":
                number = max(1, min(1000000000, number))
            elif key == "level_xp_base":
                number = max(1, min(1000000, number))
            else:
                number = max(0, min(100000000, number))
            clean[key] = number
        elif isinstance(default, str):
            text_value = str(value).strip()
            clean[key] = text_value[:1000]
        else:
            clean[key] = default
    return clean


async def _write_admin_audit(actor: dict, action: str, target: str = "", details: dict | None = None) -> None:
    try:
        security_doc = await db["miniapp_settings"].find_one({"_id": "security"})
        security = (security_doc or {}).get("data") or {}
        if security.get("audit_log_enabled", True) is False:
            return
        await db["admin_audit_log"].insert_one({
            "actor_id": int(actor.get("id", 0) or 0),
            "actor_name": str(actor.get("username") or actor.get("first_name") or "Owner")[:100],
            "action": str(action)[:100],
            "target": str(target)[:120],
            "details": details or {},
            "created_at": int(time.time()),
        })
    except Exception:
        logger.exception("Could not write Owner Panel audit record for action %s", action)


async def api_admin_settings(request: web.Request) -> web.Response:
    actor = await _admin_identity(request)
    if request.method == "GET":
        settings = await _get_admin_settings()
        return web.json_response({"settings": settings})
    try:
        body = await request.json()
        category = str(body.get("category", ""))
        incoming = body.get("values")
        clean = _sanitize_settings(category, incoming)
    except (ValueError, TypeError, AttributeError) as exc:
        raise web.HTTPBadRequest(text=json.dumps({"error": str(exc)}), content_type="application/json")
    await db["miniapp_settings"].update_one(
        {"_id": category}, {"$set": {"data": clean, "updated_at": int(time.time()), "updated_by": actor["id"]}},
        upsert=True,
    )
    _invalidate_admin_settings_cache()
    # Keep the existing bot maintenance switch in sync with Mini App settings.
    if category == "general":
        await db["bot_settings"].update_one(
            {"_id": "maintenance"},
            {"$set": {"enabled": bool(clean.get("maintenance_mode", False)), "updated_at": int(time.time())}},
            upsert=True,
        )
    await _write_admin_audit(actor, "settings.update", category, {"category": category})
    return web.json_response({"ok": True, "category": category, "values": clean})


async def api_admin_audit(request: web.Request) -> web.Response:
    await _admin_identity(request)
    try:
        limit = max(1, min(100, int(request.query.get("limit", "50"))))
    except ValueError:
        limit = 50
    rows = await db["admin_audit_log"].find(
        {}, {"_id": 0, "actor_id": 1, "actor_name": 1, "action": 1, "target": 1, "details": 1, "created_at": 1}
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return web.json_response({"events": rows})


async def api_admin_user_detail(request: web.Request) -> web.Response:
    await _admin_identity(request)
    try:
        uid = int(request.match_info["user_id"])
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid user ID."}), content_type="application/json")
    user = await user_collection.find_one(
        {"id": uid}, {"_id": 0, "id": 1, "username": 1, "first_name": 1, "coins": 1,
                      "xp": 1, "wins": 1, "characters": 1, "miniapp_suspended": 1, "created_at": 1, "last_miniapp_active": 1}
    )
    if not user:
        raise web.HTTPNotFound(text=json.dumps({"error": "User not found."}), content_type="application/json")
    user["character_count"] = len(user.get("characters", []) or [])
    user.pop("characters", None)
    created_at = user.get("created_at")
    if created_at is not None and not isinstance(created_at, (str, int, float)):
        user["created_at"] = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    return web.json_response({"user": user})


async def api_admin_user_action(request: web.Request) -> web.Response:
    actor = await _admin_identity(request)
    try:
        body = await request.json()
        uid = int(body.get("user_id"))
        action = str(body.get("action", "")).strip()
        amount = int(body.get("amount", 0) or 0)
        reason = str(body.get("reason", "")).strip()[:300]
    except (TypeError, ValueError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Provide a numeric user ID and valid action."}), content_type="application/json")
    if uid <= 0 or uid == actor["id"]:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid target user."}), content_type="application/json")
    if not reason:
        raise web.HTTPBadRequest(text=json.dumps({"error": "A reason is required for admin changes."}), content_type="application/json")
    if action in ("coins", "xp"):
        security_doc = await db["miniapp_settings"].find_one({"_id": "security"})
        security = (security_doc or {}).get("data") or {}
        try:
            max_adjustment = max(1, min(1000000, int(security.get("max_bulk_adjustment", 1000000))))
        except (TypeError, ValueError):
            max_adjustment = 1000000
        if amount == 0 or abs(amount) > max_adjustment:
            raise web.HTTPBadRequest(text=json.dumps({"error": f"Adjustment must be a non-zero amount within {max_adjustment:,}."}), content_type="application/json")
        filt = {"id": uid}
        field = "coins" if action == "coins" else "xp"
        if amount < 0:
            filt[field] = {"$gte": abs(amount)}
        result = await user_collection.update_one(filt, {"$inc": {field: amount}})
        if not result.modified_count:
            exists = await user_collection.find_one({"id": uid}, {"_id": 1})
            if not exists:
                raise web.HTTPNotFound(text=json.dumps({"error": "User not found."}), content_type="application/json")
            raise web.HTTPBadRequest(text=json.dumps({"error": f"User has insufficient {field} for this deduction."}), content_type="application/json")
    elif action in ("suspend", "unsuspend"):
        result = await user_collection.update_one(
            {"id": uid}, {"$set": {"miniapp_suspended": action == "suspend", "miniapp_suspension_reason": reason}}
        )
        if not result.matched_count:
            raise web.HTTPNotFound(text=json.dumps({"error": "User not found."}), content_type="application/json")
    else:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Unsupported user action."}), content_type="application/json")
    await _write_admin_audit(actor, f"user.{action}", str(uid), {"amount": amount, "reason": reason})
    return web.json_response({"ok": True, "user_id": uid, "action": action})


async def api_admin_upload_character_image(request: web.Request) -> web.Response:
    actor = await _character_admin_identity(request)
    media_doc = await db["miniapp_settings"].find_one({"_id": "media"})
    media = (media_doc or {}).get("data") or {}
    if media.get("telegram_image_proxy", True) is False:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Enable the Telegram image proxy before uploading character images."}),
            content_type="application/json",
        )
    try:
        max_mb = max(1, min(24, int(media.get("max_image_upload_mb", 10))))
    except (TypeError, ValueError):
        max_mb = 10
    try:
        reader = await request.multipart()
        part = await reader.next()
        while part is not None and part.name != "file":
            part = await reader.next()
        if part is None or not part.filename:
            raise ValueError("Choose an image file.")
        filename = Path(part.filename).name[:120]
        mime = str(part.headers.get("Content-Type", "")).lower()
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValueError("Supported formats: JPG, PNG, WEBP or GIF.")
        data = await part.read(decode=False)
        if not data:
            raise ValueError("The selected image is empty.")
        if len(data) > max_mb * 1024 * 1024:
            raise ValueError(f"Image is too large. Maximum size is {max_mb} MB.")
    except ValueError as exc:
        raise web.HTTPBadRequest(text=json.dumps({"error": str(exc)}), content_type="application/json")

    try:
        post = await application.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=InputFile(BytesIO(data), filename=filename),
            caption="Character image uploaded from the Owner Panel.",
            disable_notification=True,
        )
    except Exception as exc:
        logger.exception("Owner image upload failed (%s)", type(exc).__name__)
        raise web.HTTPBadGateway(
            text=json.dumps({"error": "Telegram could not upload this image. Check the character channel and bot permissions."}),
            content_type="application/json",
        ) from exc
    if not post.photo:
        raise web.HTTPBadGateway(
            text=json.dumps({"error": "Telegram did not return a usable photo."}),
            content_type="application/json",
        )
    file_id = post.photo[-1].file_id
    await _write_admin_audit(
        actor, "character.image.upload", str(post.message_id),
        {"filename": filename, "bytes": len(data), "mime": mime},
    )
    return web.json_response({
        "ok": True, "file_id": file_id, "message_id": post.message_id,
        "filename": filename, "bytes": len(data), "max_mb": max_mb,
    })


async def api_admin_character_create(request: web.Request) -> web.Response:
    actor = await _character_admin_identity(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()[:100]
        anime = str(body.get("anime", "")).strip()[:100]
        rarity = str(body.get("rarity", "⚪ Common")).strip()[:80]
        image_ref = str(body.get("img_url", "")).strip()[:2000]
        requested_id = str(body.get("id", "")).strip()[:32]
        message_id = int(body.get("message_id", 0) or 0)
    except (AttributeError, TypeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid character payload."}), content_type="application/json")
    if not name or not anime:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Character name and anime are required."}), content_type="application/json")
    if image_ref and image_ref.startswith("//"):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Use a full HTTPS image URL or Telegram file ID."}), content_type="application/json")
    if requested_id:
        char_id = requested_id
        if await collection.find_one({"id": char_id}, {"_id": 1}) or (char_id.isdigit() and await collection.find_one({"id": int(char_id)}, {"_id": 1})):
            raise web.HTTPConflict(text=json.dumps({"error": "That character ID already exists."}), content_type="application/json")
    else:
        counter = await db.sequences.find_one_and_update(
            {"_id": "character_id"}, {"$inc": {"sequence_value": 1}},
            upsert=True, return_document=True,
        )
        char_id = str(counter.get("sequence_value", 1)).zfill(4)
        for _ in range(5):
            exists = await collection.find_one({"id": char_id}, {"_id": 1})
            if not exists:
                break
            counter = await db.sequences.find_one_and_update(
                {"_id": "character_id"}, {"$inc": {"sequence_value": 1}},
                upsert=True, return_document=True,
            )
            char_id = str(counter.get("sequence_value", 1)).zfill(4)
    char = {"id": char_id, "name": name, "anime": anime, "rarity": rarity, "img_url": image_ref}
    if message_id > 0:
        char["message_id"] = message_id
    await collection.insert_one(char)
    await _write_admin_audit(actor, "character.create", char_id, {"name": name, "anime": anime, "rarity": rarity})
    return web.json_response({"ok": True, "character": _admin_character(char)}, status=201)


async def api_admin_character_update(request: web.Request) -> web.Response:
    actor = await _character_admin_identity(request)
    char_id = str(request.match_info.get("char_id", "")).strip()
    candidates = [{"id": char_id}]
    if char_id.isdigit():
        candidates.append({"id": int(char_id)})
    char = await collection.find_one({"$or": candidates})
    if not char:
        raise web.HTTPNotFound(text=json.dumps({"error": "Character not found."}), content_type="application/json")
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid character payload."}), content_type="application/json")
    updates = {}
    for field, limit in (("name", 100), ("anime", 100), ("rarity", 80), ("img_url", 2000)):
        if field in body:
            value = str(body[field]).strip()[:limit]
            if field in ("name", "anime") and not value:
                raise web.HTTPBadRequest(text=json.dumps({"error": f"{field} cannot be empty."}), content_type="application/json")
            if field == "img_url" and value.startswith("//"):
                raise web.HTTPBadRequest(text=json.dumps({"error": "Use a full HTTPS image URL or Telegram file ID."}), content_type="application/json")
            updates[field] = value
    if "message_id" in body:
        try:
            message_id = int(body.get("message_id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        updates["message_id"] = message_id if message_id > 0 else None
    if not updates:
        raise web.HTTPBadRequest(text=json.dumps({"error": "No editable fields provided."}), content_type="application/json")
    await collection.update_one({"_id": char["_id"]}, {"$set": updates})
    char.update(updates)
    await _write_admin_audit(actor, "character.update", char_id, {"fields": sorted(updates)})
    return web.json_response({"ok": True, "character": _admin_character(char)})


async def api_admin_character_delete(request: web.Request) -> web.Response:
    actor = await _character_admin_identity(request)
    char_id = str(request.match_info.get("char_id", "")).strip()
    candidates = [{"id": char_id}]
    if char_id.isdigit():
        candidates.append({"id": int(char_id)})
    char = await collection.find_one({"$or": candidates})
    if not char:
        raise web.HTTPNotFound(text=json.dumps({"error": "Character not found."}), content_type="application/json")
    await collection.delete_one({"_id": char["_id"]})
    await _write_admin_audit(actor, "character.delete", char_id, {"name": str(char.get("name", ""))})
    return web.json_response({"ok": True, "id": char_id})


async def api_admin_health(request: web.Request) -> web.Response:
    await _admin_identity(request)
    database = "ok"
    try:
        await db.command("ping")
    except Exception as exc:
        database = "error"
        logger.warning("Owner Panel health check failed: %s", type(exc).__name__)
    return web.json_response({
        "api": "ok", "database": database,
        "server_time": int(time.time()), "database_name": str(getattr(db, "name", "configured")),
    })


async def api_admin_logs(request: web.Request) -> web.Response:
    await _admin_identity(request)
    try:
        limit = max(1, min(200, int(request.query.get("limit", "100"))))
    except ValueError:
        limit = 100
    log_path = Path("logs/bot.log")
    if not log_path.is_file():
        return web.json_response({"lines": [], "available": False, "message": "Local log file is not available on this service instance."})
    def read_tail():
        try:
            return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except OSError:
            return []
    lines = await asyncio.to_thread(read_tail)
    return web.json_response({"lines": lines, "available": True})


async def _require_feature(name: str) -> None:
    settings = await _get_admin_settings()
    game = settings.get("game", {})
    features = settings.get("features", {})
    aliases = {"market": game.get("market_enabled", True), "quiz": game.get("quiz_enabled", True), "mines": game.get("mines_enabled", True)}
    enabled = aliases.get(name, True) and features.get(name, True)
    if not enabled:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": f"{name.title()} is temporarily disabled by the bot owner."}),
            content_type="application/json",
        )


async def api_user_preferences(request: web.Request) -> web.Response:
    tg_user = await _identity(request)
    await _require_feature("user_settings")
    uid = tg_user["id"]
    user = await user_collection.find_one({"id": uid}, {"miniapp_preferences": 1})
    current = dict(_USER_PREFERENCE_DEFAULTS)
    general_doc = await db["miniapp_settings"].find_one({"_id": "general"})
    default_language = str(((general_doc or {}).get("data") or {}).get("default_language", "en")).strip().lower()
    current["language"] = default_language if default_language in {"en", "hi"} else "en"
    saved = (user or {}).get("miniapp_preferences", {})
    if isinstance(saved, dict):
        current.update({k: v for k, v in saved.items() if k in _USER_PREFERENCE_DEFAULTS})
    if request.method == "GET":
        return web.json_response({"preferences": current})
    try:
        body = await request.json()
        incoming = body.get("preferences", {})
        if not isinstance(incoming, dict):
            raise ValueError("Preferences must be an object.")
    except (ValueError, AttributeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid preferences."}), content_type="application/json")
    allowed = {
        "theme": {"system", "light", "dark"},
        "accent": {"lavender", "pink", "blue", "mint"},
        "language": {"en", "hi"},
        "profile_visibility": {"public", "private"},
        "default_sort": {"rarity", "name", "recent"},
    }
    clean = {}
    for key, default in _USER_PREFERENCE_DEFAULTS.items():
        value = incoming.get(key, current.get(key, default))
        if isinstance(default, bool):
            clean[key] = value is True
        elif key in allowed:
            clean[key] = value if value in allowed[key] else default
        else:
            clean[key] = default
    await user_collection.update_one(
        {"id": uid},
        {"$set": {"miniapp_preferences": clean},
         "$setOnInsert": {"id": uid, "characters": [], "coins": 0, "xp": 0, "wins": 0, "favorites": []}},
        upsert=True,
    )
    return web.json_response({"ok": True, "preferences": clean})


def register_miniapp_routes(app: web.Application) -> None:
    app.middlewares.append(miniapp_cors)
    app.router.add_get("/api/miniapp/status", api_status)
    app.router.add_get("/api/admin/overview", api_admin_overview)
    app.router.add_get("/api/miniapp/public-config", api_public_config)
    app.router.add_route("GET", "/api/admin/broadcasts", api_admin_broadcasts)
    app.router.add_route("POST", "/api/admin/broadcasts", api_admin_broadcasts)
    app.router.add_get("/api/admin/export", api_admin_export)
    app.router.add_post("/api/admin/restore", api_admin_restore)
    app.router.add_route("GET", "/api/admin/redeem-codes", api_admin_redeem_codes)
    app.router.add_route("POST", "/api/admin/redeem-codes", api_admin_redeem_codes)
    app.router.add_route("DELETE", "/api/admin/redeem-codes", api_admin_redeem_codes)
    app.router.add_post("/api/admin/users/bulk-action", api_admin_bulk_user_action)
    app.router.add_route("GET", "/api/admin/settings", api_admin_settings)
    app.router.add_route("POST", "/api/admin/settings", api_admin_settings)
    app.router.add_get("/api/admin/audit", api_admin_audit)
    app.router.add_get("/api/admin/health", api_admin_health)
    app.router.add_get("/api/admin/logs", api_admin_logs)
    app.router.add_get("/api/admin/users/{user_id}", api_admin_user_detail)
    app.router.add_post("/api/admin/users/action", api_admin_user_action)
    app.router.add_post("/api/admin/characters/upload-image", api_admin_upload_character_image)
    app.router.add_post("/api/admin/characters", api_admin_character_create)
    app.router.add_put("/api/admin/characters/{char_id}", api_admin_character_update)
    app.router.add_delete("/api/admin/characters/{char_id}", api_admin_character_delete)

    app.router.add_get("/api/admin/users", api_admin_users)
    app.router.add_get("/api/admin/characters", api_admin_characters)
    app.router.add_post("/api/admin/coins", api_admin_adjust_coins)
    app.router.add_get("/api/miniapp/me", api_me)
    app.router.add_get("/api/miniapp/avatar/{user_id}", api_user_avatar)
    app.router.add_get("/api/miniapp/collection", api_user_collection)
    app.router.add_route("GET", "/api/miniapp/preferences", api_user_preferences)
    app.router.add_route("POST", "/api/miniapp/preferences", api_user_preferences)
    app.router.add_route("GET", "/api/admin/store-characters", api_admin_store_characters)
    app.router.add_route("POST", "/api/admin/store-characters", api_admin_store_characters)
    app.router.add_route("PUT", "/api/admin/store-characters/{char_id}", api_admin_store_character)
    app.router.add_route("DELETE", "/api/admin/store-characters/{char_id}", api_admin_store_character)
    app.router.add_get("/api/miniapp/store", api_store_list)
    app.router.add_post("/api/miniapp/store/buy", api_store_buy)
    app.router.add_get("/api/miniapp/market", api_market)
    app.router.add_post("/api/miniapp/market/buy", api_market_buy)
    app.router.add_post("/api/miniapp/quiz/start", api_quiz_start)
    app.router.add_post("/api/miniapp/games/rarity-duel/start", api_rarity_duel_start)
    app.router.add_post("/api/miniapp/games/rarity-duel/answer", api_rarity_duel_answer)
    app.router.add_post("/api/miniapp/games/memory-match/start", api_memory_match_start)
    app.router.add_post("/api/miniapp/games/memory-match/reveal", api_memory_match_reveal)
    app.router.add_post("/api/miniapp/games/{game}/start", api_extra_game_start)
    app.router.add_post("/api/miniapp/games/{game}/answer", api_extra_game_answer)
    app.router.add_get("/api/miniapp/character-image/{char_id}", api_character_image)
    app.router.add_post("/api/miniapp/quiz/answer", api_quiz_answer)
    app.router.add_post("/api/miniapp/mines/start", api_mines_start)
    app.router.add_post("/api/miniapp/mines/reveal", api_mines_reveal)
    app.router.add_post("/api/miniapp/mines/finish", api_mines_finish)
