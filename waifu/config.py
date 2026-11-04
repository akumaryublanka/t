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

    # Active rarities (used by upload, harem, drop, claim, and rarity stats)
    RARITY_MAP: dict[int, str] = {
        1: "⚪ Common",
        2: "🟢 Medium",
        3: "🟣 Rare",
        4: "🔵 Epic",
        5: "🟡 Legendary",
        6: "🌟 Luminous",
        7: "🎐 Celestial",
        8: "💮 Special Edition",
        9: "🔮 Limited Edition",
        10: "🔞 Extreme",
        11: "❄️ Winter",
        12: "☀️ Summer",
        13: "🏅 Elite",
        14: "🎭 Cosplay",
        15: "🎃 Halloween",
        16: "💝 Valentine's",
    }

    @classmethod
    def all_sudo(cls) -> set[int]:
        return {cls.OWNER_ID, *cls.sudo_users}
        
