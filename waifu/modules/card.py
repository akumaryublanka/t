"""Holographic collectible cards for MikasaVerse characters."""
from io import BytesIO
from html import escape

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection
from waifu.config import Config
from waifu.image_watermark import _load_watermark, watermark_image_bytes

_MAX_BYTES = 15 * 1024 * 1024
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(_FONT_BOLD if bold else _FONT_REGULAR, size=size)
    except OSError:
        return ImageFont.load_default()


async def _image_bytes(bot, reference: str) -> bytes:
    if reference.startswith(("https://", "http://")):
        timeout = aiohttp.ClientTimeout(total=20, connect=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(reference, allow_redirects=True) as response:
                response.raise_for_status()
                data = await response.content.read(_MAX_BYTES + 1)
    else:
        file = await bot.get_file(reference)
        data = bytes(await file.download_as_bytearray())
    if not data or len(data) > _MAX_BYTES:
        raise ValueError("Image is empty or exceeds the size limit")
    return data


def _rarity_label(value) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        number = value if value in Config.RARITY_MAP else Config.RARITY_LEGACY_NUMBER_MAP.get(value, 1)
        return Config.RARITY_MAP.get(number, Config.RARITY_MAP[1])
    raw = str(value or Config.RARITY_MAP[1]).strip()
    if raw.isdigit():
        number = int(raw)
        number = number if number in Config.RARITY_MAP else Config.RARITY_LEGACY_NUMBER_MAP.get(number, 1)
        return Config.RARITY_MAP.get(number, Config.RARITY_MAP[1])
    return Config.RARITY_LEGACY_MAP.get(raw, raw)


def render_holographic_card(image_data: bytes, char: dict) -> BytesIO:
    with Image.open(BytesIO(image_data)) as source:
        source.seek(0)
        art = ImageOps.fit(source.convert("RGB"), (760, 820), method=Image.Resampling.LANCZOS).convert("RGBA")

    # Soft, diagonal foil bands and sparkles give the artwork a holographic finish.
    foil = Image.new("RGBA", art.size, (0, 0, 0, 0))
    foil_draw = ImageDraw.Draw(foil)
    bands = [
        (255, 105, 190, 32),
        (80, 220, 255, 28),
        (190, 130, 255, 30),
        (255, 220, 120, 24),
    ]
    for index, color in enumerate(bands):
        offset = index * 185 - 260
        foil_draw.polygon(
            [(offset, 0), (offset + 105, 0), (offset + 470, 820), (offset + 365, 820)],
            fill=color,
        )
    seed = sum(ord(ch) for ch in str(char.get("id", "")))
    for index in range(18):
        x = (seed * (index + 7) * 37) % 740 + 10
        y = (seed * (index + 11) * 53) % 800 + 10
        radius = 2 + (index % 3)
        foil_draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(255, 255, 255, 110))
    art = Image.alpha_composite(art, foil)

    width, height = 820, 1080
    card = Image.new("RGBA", (width, height), (16, 10, 34, 255))
    card.alpha_composite(art, (30, 30))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((5, 5, width-6, height-6), radius=34, outline=(255, 115, 205, 255), width=8)
    draw.rounded_rectangle((17, 17, width-18, height-18), radius=27, outline=(105, 220, 255, 230), width=3)

    # Bottom information plate is part of the card design, not a watermark box.
    draw.rectangle((30, 850, width-30, height-30), fill=(22, 13, 45, 255))
    name = str(char.get("name", "Unknown"))
    anime = str(char.get("anime", "Unknown"))
    rarity = _rarity_label(char.get("rarity", Config.RARITY_MAP[1]))
    name_size = 52
    while name_size > 26 and draw.textbbox((0, 0), name, font=_font(name_size, True))[2] > 740:
        name_size -= 2
    draw.text((55, 875), name, font=_font(name_size, True), fill=(255, 255, 255, 255))
    anime_size = 28
    while anime_size > 16 and draw.textbbox((0, 0), anime, font=_font(anime_size))[2] > 710:
        anime_size -= 2
    draw.text((58, 947), anime, font=_font(anime_size), fill=(221, 195, 255, 255))
    draw.text((58, 1000), rarity.upper(), font=_font(24, True), fill=(255, 210, 135, 255))
    draw.text((width-250, 1000), f"NO. {char.get('id', '—')}", font=_font(20, True), fill=(180, 225, 255, 255))

    # Reuse the existing MikasaVerse logo, with the same transparent matte key.
    logo = _load_watermark().copy()
    if logo.width and logo.height:
        logo.thumbnail((175, 58), Image.Resampling.LANCZOS)
        card.alpha_composite(logo, (width-220, 875))

    output = BytesIO()
    card.convert("RGB").save(output, format="JPEG", quality=94, optimize=True)
    output.name = "mikasaverse_holographic_card.jpg"
    output.seek(0)
    return output


async def card_command(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    user = update.effective_user
    if not context.args:
        await message.reply_text("Usage: <code>/card character_id</code>", parse_mode=ParseMode.HTML)
        return
    char_id = context.args[0].strip()
    player = await user_collection.find_one({"id": user.id}) or {}
    char = next((c for c in player.get("characters", []) if str(c.get("id")) == char_id), None)
    if not char:
        char = await collection.find_one({"id": char_id})
    if not char:
        await message.reply_text("❌ Character ID not found.")
        return
    image_ref = str(char.get("img_url", "") or "").strip()
    if not image_ref:
        await message.reply_text("❌ This character has no image attached.")
        return
    try:
        raw = await _image_bytes(context.bot, image_ref)
        branded = watermark_image_bytes(raw)
        card = render_holographic_card(branded, char)
        await message.reply_photo(
            photo=card,
            caption=f"🎴 <b>{escape(str(char.get('name', 'Character')))}</b> — Collector's Foil",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        await message.reply_text("❌ Couldn't render this card right now. Please try again later.")
        from waifu import LOGGER
        LOGGER.warning("Holographic card render failed for %s (%s)", char_id, type(exc).__name__)


application.add_handler(CommandHandler("card", card_command, block=False))
