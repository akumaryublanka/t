import os
from dotenv import load_dotenv

load_dotenv()


def _req(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if not v:
        raise RuntimeError(f"Required env var '{key}' not set. Copy .env.example → .env")
    return v


def _int_list(key: str) -> list[int]:
    return [int(x.strip()) for x in os.environ.get(key, "").split(",")
            if x.strip().lstrip("-").isdigit()]


class Config:
    TOKEN:            str       = _req("BOT_TOKEN")
    BOT_USERNAME:     str       = _req("BOT_USERNAME")
    OWNER_ID:         int       = int(_req("OWNER_ID"))
    sudo_users:       list[int] = _int_list("SUDO_IDS")
    GROUP_ID:         int       = int(_req("GROUP_ID"))
    CHARA_CHANNEL_ID: int       = int(_req("CHARA_CHANNEL_ID"))
    mongo_url:        str       = _req("MONGO_URI")
    DB_NAME:          str       = os.environ.get("DB_NAME", "waifu_bot")
    SUPPORT_CHAT:     str       = os.environ.get("SUPPORT_CHAT", "")
    UPDATE_CHAT:      str       = os.environ.get("UPDATE_CHAT",  "")
    PHOTO_URL:        list[str] = [u.strip() for u in
                                   os.environ.get("PHOTO_URLS", "").split(",")
                                   if u.strip().startswith("http")]
    DROP_INTERVAL_MIN:    int   = int(os.environ.get("DROP_INTERVAL_MINUTES", "15"))
    DEFAULT_MSG_FREQUENCY: int  = int(os.environ.get("DEFAULT_MSG_FREQUENCY", "20"))

    # Economy — NekoVerse currencies
    # 🟡 Neko Coin is the regular currency.
    # 💎 Neko Gem is the rare currency.
    DAILY_NEKO_COINS:     int = 200
    DUEL_WIN_NEKO_COINS:  int = 150
    DUEL_LOSE_NEKO_COINS: int = 30

    # Exchange rates
    NEKO_COINS_PER_GEM: int = 1000
    NEKO_COINS_PER_REVERSE_GEM: int = 900

    # Canonical MikasaVerse rarities. IDs 1–16 stay stable for existing characters.
    RARITY_MAP: dict[int, str] = {
        1: "🌫️ Ordinary",
        2: "🍀 Superior",
        3: "🔮 Prime",
        4: "🟣 Epic",
        5: "🟡 Legendary",
        6: "🌟 Mythic",
        7: "💠 Celestial",
        8: "💮 Special Edition",
        9: "🪽 Limited Edition",
        10: "🔞 Extreme",
        11: "❄️ Winter",
        12: "☀️ Summer",
        13: "👑 Elite",
        14: "🎭 Cosplay",
        15: "🎃 Halloween",
        16: "💝 Valentine's",
    }

    # Collection prestige values shown in /profile and /rarity.
    RARITY_VALUE_MAP: dict[int, int] = {
        1: 100,
        2: 250,
        3: 500,
        4: 1_000,
        5: 2_500,
        6: 55_000,
        7: 45_000,
        8: 35_000,
        9: 75_000,
        10: 100_000,
        11: 10_000,
        12: 12_000,
        13: 15_000,
        14: 18_000,
        15: 20_000,
        16: 25_000,
    }

    # Per-character spawn weights: lower weight means a rarer drop.
    # Extreme is intentionally the hardest rarity to obtain.
    RARITY_WEIGHT_MAP: dict[int, float] = {
        1: 1_000,
        2: 500,
        3: 250,
        4: 100,
        5: 40,
        6: 1.8,
        7: 2.2,
        8: 2.8,
        9: 1.0,
        10: 0.1,
        11: 10,
        12: 8,
        13: 7,
        14: 6,
        15: 5,
        16: 4,
    }

    # Old database labels are normalized to their new display labels.
    # This keeps existing characters, claims, and collections compatible.
    RARITY_LEGACY_MAP: dict[str, str] = {
        "⚪ Common": RARITY_MAP[1],
        "⚪️ Common": RARITY_MAP[1],
        "Common": RARITY_MAP[1],
        "🟢 Medium": RARITY_MAP[2],
        "Medium": RARITY_MAP[2],
        "🟣 Rare": RARITY_MAP[3],
        "Rare": RARITY_MAP[3],
        "🔵 Epic": RARITY_MAP[4],
        "Epic": RARITY_MAP[4],
        "🟡 Legendary": RARITY_MAP[5],
        "Legendary": RARITY_MAP[5],
        "🌟 Luminous": RARITY_MAP[6],
        "Luminous": RARITY_MAP[6],
        "🎐 Celestial": RARITY_MAP[7],
        "Celestial": RARITY_MAP[7],
        "💮 Special Edition": RARITY_MAP[8],
        "Special Edition": RARITY_MAP[8],
        "🔮 Limited Edition": RARITY_MAP[9],
        "Limited Edition": RARITY_MAP[9],
        "🔞 Extreme": RARITY_MAP[10],
        "Extreme": RARITY_MAP[10],
        "❄️ Winter": RARITY_MAP[11],
        "Winter": RARITY_MAP[11],
        "☀️ Summer": RARITY_MAP[12],
        "Summer": RARITY_MAP[12],
        "🏅 Elite": RARITY_MAP[13],
        "Elite": RARITY_MAP[13],
        "🎭 Cosplay": RARITY_MAP[14],
        "Cosplay": RARITY_MAP[14],
        "🎃 Halloween": RARITY_MAP[15],
        "Halloween": RARITY_MAP[15],
        "💝 Valentine's": RARITY_MAP[16],
        "Valentine's": RARITY_MAP[16],
        "🎄 Christmas": RARITY_MAP[13],
        "Christmas": RARITY_MAP[13],
    }

    @classmethod
    def all_sudo(cls) -> set[int]:
        return {cls.OWNER_ID, *cls.sudo_users}
        
