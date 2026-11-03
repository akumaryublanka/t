"""Unit tests for Telegram Mini App initData signature and freshness validation."""
import ast
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import time
import types
import unittest
from aiohttp.test_utils import TestClient, TestServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

# Isolate this module from the live bot config and database during unit tests.
fake_waifu = types.ModuleType("waifu")
fake_waifu.__path__ = []
fake_waifu.TOKEN = "123456:test-token"
fake_waifu.application = types.SimpleNamespace(bot=object())
fake_waifu.CHARA_CHANNEL_ID = -1001234567890
fake_waifu.collection = object()
fake_waifu.user_collection = object()
fake_waifu.market_collection = object()
fake_waifu.db = object()
sys.modules["waifu"] = fake_waifu

fake_bson = types.ModuleType("bson")


class ObjectId:
    @staticmethod
    def is_valid(value):
        return isinstance(value, str) and len(value) == 24

    def __init__(self, value):
        self.value = value


fake_bson.ObjectId = ObjectId
sys.modules["bson"] = fake_bson

MODULE_PATH = Path(__file__).resolve().parents[1] / "waifu" / "miniapp_api.py"
spec = importlib.util.spec_from_file_location("miniapp_api_under_test", MODULE_PATH)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def signed_init_data(user_id=42, auth_date=None, token=None):
    """Create initData using the same decoded key/value representation Telegram signs."""
    auth_date = int(time.time()) if auth_date is None else int(auth_date)
    token = token or api.TOKEN
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAExample",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    # Canonicalize exactly as verify_init_data does after parsing URL-encoded input.
    encoded = urlencode(fields)
    decoded_fields = dict(parse_qsl(encoded, keep_blank_values=True, strict_parsing=True))
    check = "\n".join(f"{key}={decoded_fields[key]}" for key in sorted(decoded_fields))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    decoded_fields["hash"] = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(decoded_fields)


class InitDataVerificationTests(unittest.TestCase):
    def test_accepts_valid_signed_data(self):
        user = api.verify_init_data(signed_init_data(user_id=321), now=int(time.time()))
        self.assertEqual(user["id"], 321)

    def test_rejects_tampered_user(self):
        raw = signed_init_data().replace("Test", "Evil")
        with self.assertRaisesRegex(ValueError, "signature"):
            api.verify_init_data(raw, now=int(time.time()))

    def test_rejects_expired_data(self):
        now = int(time.time())
        raw = signed_init_data(auth_date=now - api.MAX_INIT_DATA_AGE - 1)
        with self.assertRaisesRegex(ValueError, "expired"):
            api.verify_init_data(raw, now=now)

    def test_rejects_future_auth_date(self):
        now = int(time.time())
        raw = signed_init_data(auth_date=now + 120)
        with self.assertRaisesRegex(ValueError, "expired"):
            api.verify_init_data(raw, now=now)

    def test_rejects_duplicate_fields(self):
        raw = signed_init_data() + "&auth_date=1"
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            api.verify_init_data(raw, now=int(time.time()))

    def test_rejects_missing_signature(self):
        with self.assertRaises(ValueError):
            api.verify_init_data("user=%7B%22id%22%3A42%7D&auth_date=1", now=int(time.time()))


class AdminSettingsValidationTests(unittest.TestCase):
    def test_rarity_settings_require_one_enabled_weight(self):
        with self.assertRaisesRegex(ValueError, "At least one rarity"):
            api._sanitize_settings("rarities", [
                {"emoji": "⚪", "name": "Common", "weight": 0, "enabled": False, "kind": "main"}
            ])

    def test_rarity_settings_deduplicate_names_and_bound_weights(self):
        result = api._sanitize_settings("rarities", [
            {"emoji": "⚪", "name": "Common", "weight": 999999, "enabled": True, "kind": "main"},
            {"emoji": "🟢", "name": "common", "weight": 1, "enabled": True, "kind": "main"},
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["weight"], 100000)

    def test_game_settings_use_correct_mines_quiz_and_cooldown_bounds(self):
        values = api._sanitize_settings("game", {
            "bot_mines_multiplier_1_4_percent": 1200,
            "daily_cooldown_seconds": 604800,
            "bot_quiz_easy_timeout": 999,
        })
        self.assertEqual(values["bot_mines_multiplier_1_4_percent"], 1000)
        self.assertEqual(values["daily_cooldown_seconds"], 604800)
        self.assertEqual(values["bot_quiz_easy_timeout"], 300)

    def test_changed_bot_modules_are_valid_python(self):
        root = Path(__file__).resolve().parents[1]
        paths = [
            "waifu/modules/waifu_drop.py",
            "waifu/modules/quiz.py",
            "waifu/modules/mine.py",
            "waifu/modules/nguess.py",
            "waifu/modules/economy.py",
            "waifu/modules/duel.py",
            "waifu/modules/trade.py",
            "waifu/modules/hclaim.py",
            "waifu/modules/redeem.py",
            "waifu/modules/profile.py",
            "waifu/__main__.py",
        ]
        for relative in paths:
            with self.subTest(path=relative):
                ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)


class MiniAppHttpSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = api.web.Application(middlewares=[api.miniapp_cors])
        api.register_miniapp_routes(app)
        app.router.add_route("OPTIONS", "/{tail:.*}", lambda request: api.web.Response(status=204))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_status_route_is_live(self):
        response = await self.client.get("/api/miniapp/status")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["service"], "adamwaifus-miniapp-api")

    async def test_admin_overview_rejects_non_owner(self):
        previous = os.environ.get("OWNER_ID")
        os.environ["OWNER_ID"] = "999999"
        try:
            response = await self.client.get(
                "/api/admin/overview",
                headers={"X-Telegram-Init-Data": signed_init_data(user_id=42)},
            )
            self.assertEqual(response.status, 403)
            payload = await response.json()
            self.assertIn("Owner access only", payload["error"])
        finally:
            if previous is None:
                os.environ.pop("OWNER_ID", None)
            else:
                os.environ["OWNER_ID"] = previous

    async def test_admin_redeem_codes_rejects_non_owner(self):
        previous = os.environ.get("OWNER_ID")
        os.environ["OWNER_ID"] = "999999"
        try:
            response = await self.client.get(
                "/api/admin/redeem-codes",
                headers={"X-Telegram-Init-Data": signed_init_data(user_id=42)},
            )
            self.assertEqual(response.status, 403)
        finally:
            if previous is None:
                os.environ.pop("OWNER_ID", None)
            else:
                os.environ["OWNER_ID"] = previous

    async def test_admin_character_upload_rejects_non_owner(self):
        previous = os.environ.get("OWNER_ID")
        os.environ["OWNER_ID"] = "999999"
        try:
            response = await self.client.post(
                "/api/admin/characters/upload-image",
                headers={"X-Telegram-Init-Data": signed_init_data(user_id=42)},
            )
            self.assertEqual(response.status, 403)
        finally:
            if previous is None:
                os.environ.pop("OWNER_ID", None)
            else:
                os.environ["OWNER_ID"] = previous

    async def test_admin_restore_rejects_non_owner(self):
        previous = os.environ.get("OWNER_ID")
        os.environ["OWNER_ID"] = "999999"
        try:
            response = await self.client.post(
                "/api/admin/restore",
                json={"confirmation": "RESTORE MERGE", "backup": {}, "sections": ["users"]},
                headers={"X-Telegram-Init-Data": signed_init_data(user_id=42)},
            )
            self.assertEqual(response.status, 403)
        finally:
            if previous is None:
                os.environ.pop("OWNER_ID", None)
            else:
                os.environ["OWNER_ID"] = previous

    async def test_bulk_user_action_rejects_non_owner(self):
        previous = os.environ.get("OWNER_ID")
        os.environ["OWNER_ID"] = "999999"
        try:
            response = await self.client.post(
                "/api/admin/users/bulk-action",
                json={"user_ids": [10], "action": "coins", "amount": 1, "reason": "test"},
                headers={"X-Telegram-Init-Data": signed_init_data(user_id=42)},
            )
            self.assertEqual(response.status, 403)
        finally:
            if previous is None:
                os.environ.pop("OWNER_ID", None)
            else:
                os.environ["OWNER_ID"] = previous

    async def test_profile_rejects_missing_init_data(self):
        response = await self.client.get("/api/miniapp/me")
        self.assertEqual(response.status, 401)
        payload = await response.json()
        self.assertIn("error", payload)

    async def test_cors_preflight_is_supported(self):
        response = await self.client.options(
            "/api/miniapp/me",
            headers={
                "Origin": "https://adamwaifuminiapp.onrender.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Telegram-Init-Data",
            },
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "https://adamwaifuminiapp.onrender.com")


if __name__ == "__main__":
    unittest.main()
