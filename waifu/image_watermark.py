"""Readable MikasaVerse brand watermark for character artwork."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from waifu import LOGGER


_WATERMARK_PATH = Path(__file__).parent / "assets" / "mikasaverse_watermark.webp"
_MAX_IMAGE_BYTES = 15 * 1024 * 1024
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@lru_cache(maxsize=1)
def _load_watermark() -> Image.Image:
    with Image.open(_WATERMARK_PATH) as source:
        return source.convert("RGBA")


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        return ImageFont.load_default()


def watermark_image_bytes(data: bytes) -> bytes:
    """Apply a clear MikasaVerse • Waifu Collection label and return JPEG bytes."""
    if not data:
        raise ValueError("Cannot watermark an empty image")

    with Image.open(BytesIO(data)) as source:
        try:
            source.seek(0)
        except EOFError:
            pass
        canvas = source.convert("RGBA")

    width, height = canvas.size
    if width < 2 or height < 2:
        raise ValueError("Invalid image dimensions")

    # Reuse the existing MikasaVerse crown artwork, with the brand name and
    # collection label enlarged so the ownership is readable on Telegram.
    logo = _load_watermark().copy()
    panel_w = min(int(width * 0.58), max(220, int(width * 0.58)))
    panel_h = max(66, int(height * 0.12))
    panel_w = min(panel_w, max(1, width - 12))
    panel_h = min(panel_h, max(1, height - 12))
    margin = max(8, int(min(width, height) * 0.025))
    x0 = max(0, width - panel_w - margin)
    y0 = max(0, height - panel_h - margin)
    x1 = min(width - 1, x0 + panel_w)
    y1 = min(height - 1, y0 + panel_h)

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=max(10, int(panel_h * 0.22)),
        fill=(13, 8, 29, 232),
        outline=(226, 151, 255, 255),
        width=max(2, int(min(width, height) * 0.003)),
    )

    pad = max(10, int(panel_h * 0.16))
    text_x = x0 + pad
    main_size = max(18, int(panel_h * 0.36))
    sub_size = max(11, int(panel_h * 0.19))
    main_font = _font(_FONT_BOLD, main_size)
    sub_font = _font(_FONT_BOLD, sub_size)
    main_y = y0 + max(2, int(panel_h * 0.12))
    sub_y = y0 + int(panel_h * 0.60)
    draw.text(
        (text_x, main_y), "MIKASAVERSE",
        font=main_font, fill=(255, 255, 255, 255),
        stroke_width=1, stroke_fill=(13, 8, 29, 255),
    )
    draw.text(
        (text_x, sub_y), "WAIFU COLLECTION",
        font=sub_font, fill=(232, 190, 255, 255),
        stroke_width=1, stroke_fill=(13, 8, 29, 255),
    )

    # Keep the original crown image as a larger, clear brand accent on the right.
    if logo.width and logo.height:
        accent_w = max(20, int(panel_h * 0.62))
        accent_h = max(20, int(logo.height * accent_w / logo.width))
        accent = logo.resize((accent_w, accent_h), Image.Resampling.LANCZOS)
        accent.putalpha(accent.getchannel("A").point(lambda value: int(value * 0.96)))
        ax = max(text_x, x1 - accent_w - pad // 2)
        ay = y0 + max(0, (panel_h - accent_h) // 2)
        overlay.alpha_composite(accent, (ax, ay))

    canvas = Image.alpha_composite(canvas, overlay)
    output = BytesIO()
    canvas.convert("RGB").save(output, format="JPEG", quality=93, optimize=True)
    return output.getvalue()


async def watermarked_photo(bot, image_ref):
    """Download a URL or Telegram file_id, watermark it, and return an uploadable file.

    If downloading or processing fails, return the original reference so a temporary
    watermark issue does not stop a character drop or claim from being delivered.
    """
    if not image_ref:
        return image_ref

    source = str(image_ref).strip()
    try:
        if source.startswith(("https://", "http://")):
            timeout = aiohttp.ClientTimeout(total=20, connect=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(source, allow_redirects=True) as response:
                    response.raise_for_status()
                    data = await response.content.read(_MAX_IMAGE_BYTES + 1)
        else:
            telegram_file = await bot.get_file(source)
            data = bytes(await telegram_file.download_as_bytearray())

        if not data or len(data) > _MAX_IMAGE_BYTES:
            raise ValueError("Image is empty or exceeds the 15 MB watermark limit")

        marked = BytesIO(watermark_image_bytes(data))
        marked.name = "mikasaverse.jpg"
        marked.seek(0)
        return marked
    except Exception as exc:
        LOGGER.warning(
            "Could not watermark character image; using original reference (%s)",
            type(exc).__name__,
        )
        return image_ref
