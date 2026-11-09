"""Readable MikasaVerse brand watermark for character artwork."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from waifu import LOGGER


_WATERMARK_PATH = Path(__file__).parent / "assets" / "mikasaverse_watermark.webp"
_MAX_IMAGE_BYTES = 15 * 1024 * 1024
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@lru_cache(maxsize=1)
def _load_watermark() -> Image.Image:
    """Load the existing logo and remove its near-black matte background."""
    with Image.open(_WATERMARK_PATH) as source:
        logo = source.convert("RGBA")

    # The supplied logo artwork has a black canvas. Key out only near-neutral
    # dark pixels, preserving the purple/pink parts of the original artwork.
    cleaned = []
    for red, green, blue, alpha in logo.getdata():
        brightest = max(red, green, blue)
        darkest = min(red, green, blue)
        chroma = brightest - darkest
        if brightest <= 28 and chroma <= 20:
            alpha = 0
        elif brightest <= 64 and chroma <= 24:
            alpha = int(alpha * (brightest - 28) / 36)
        cleaned.append((red, green, blue, max(0, min(255, alpha))))
    logo.putdata(cleaned)
    return logo


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        return ImageFont.load_default()


def watermark_image_bytes(data: bytes) -> bytes:
    """Apply a larger, transparent MikasaVerse logo and return JPEG bytes."""
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

    logo = _load_watermark().copy()
    margin = max(6, int(min(width, height) * 0.025))
    if logo.width and logo.height:
        aspect = logo.width / logo.height
        # Make the logo substantially larger while keeping it inside short
        # landscape images and leaving the artwork itself unobstructed.
        logo_w = min(
            int(width * 0.42),
            int(height * 0.20 * aspect),
            max(1, width - 2 * margin),
        )
        logo_w = max(1, logo_w)
        logo_h = max(1, int(logo.height * logo_w / logo.width))
        logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)

        label_font_size = max(9, int(logo_h * 0.24))
        label_font = _font(_FONT_BOLD, label_font_size)
        label = "WAIFU COLLECTION"
        draw_probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        left, top, right, bottom = draw_probe.textbbox(
            (0, 0), label, font=label_font, stroke_width=1
        )
        label_w = right - left
        label_h = bottom - top
        gap = max(1, int(logo_h * 0.05))
        total_h = logo_h + gap + label_h

        x = max(0, width - logo_w - margin)
        y = max(0, height - total_h - margin)

        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))

        # A soft dark shadow gives the transparent logo contrast without
        # putting a black rectangle or badge behind it.
        blur_radius = max(1, int(logo_h * 0.045))
        shadow_alpha = logo.getchannel("A").filter(
            ImageFilter.GaussianBlur(radius=blur_radius)
        )
        shadow = Image.new("RGBA", logo.size, (15, 5, 28, 0))
        shadow.putalpha(shadow_alpha.point(lambda value: int(value * 0.82)))
        overlay.alpha_composite(shadow, (min(width - 1, x + 1), min(height - 1, y + 2)))
        overlay.alpha_composite(logo, (x, y))

        # Keep the collection label readable, but let the original logo remain
        # the main visual mark. No filled background is drawn behind either.
        label_x = max(0, width - margin - label_w)
        label_y = min(height - 1, y + logo_h + gap - top)
        label_draw = ImageDraw.Draw(overlay)
        label_draw.text(
            (label_x, label_y),
            label,
            font=label_font,
            fill=(247, 226, 255, 255),
            stroke_width=1,
            stroke_fill=(24, 8, 38, 235),
        )

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
