"""Small, consistent MikasaVerse watermark for character artwork."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw

from waifu import LOGGER


_WATERMARK_PATH = Path(__file__).parent / "assets" / "mikasaverse_watermark.webp"
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


@lru_cache(maxsize=1)
def _load_watermark() -> Image.Image:
    with Image.open(_WATERMARK_PATH) as source:
        return source.convert("RGBA")


def watermark_image_bytes(data: bytes) -> bytes:
    """Apply a subtle lower-right MikasaVerse logo and return a JPEG image."""
    if not data:
        raise ValueError("Cannot watermark an empty image")

    with Image.open(BytesIO(data)) as source:
        # Character collections are still photo messages; use the first GIF frame.
        try:
            source.seek(0)
        except EOFError:
            pass
        canvas = source.convert("RGBA")

    width, height = canvas.size
    if width < 2 or height < 2:
        raise ValueError("Invalid image dimensions")

    mark = _load_watermark().copy()
    target_width = max(64, min(240, int(width * 0.22)))
    target_width = min(target_width, max(48, width - 16))
    target_height = max(1, int(mark.height * target_width / mark.width))
    mark = mark.resize((target_width, target_height), Image.Resampling.LANCZOS)

    # Lower opacity keeps the character artwork clearly visible.
    alpha = mark.getchannel("A").point(lambda value: int(value * 0.68))
    mark.putalpha(alpha)

    margin = max(8, int(min(width, height) * 0.025))
    x = max(0, width - mark.width - margin)
    y = max(0, height - mark.height - margin)

    # A faint dark glass backing keeps the wordmark legible on bright artwork.
    pad_x = max(4, int(target_width * 0.025))
    pad_y = max(3, int(target_height * 0.08))
    panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle(
        (
            max(0, x - pad_x),
            max(0, y - pad_y),
            min(width - 1, x + mark.width + pad_x),
            min(height - 1, y + mark.height + pad_y),
        ),
        radius=max(5, int(target_height * 0.12)),
        fill=(12, 7, 28, 54),
        outline=(229, 139, 255, 42),
        width=1,
    )
    canvas = Image.alpha_composite(canvas, panel)
    canvas.alpha_composite(mark, (x, y))

    output = BytesIO()
    canvas.convert("RGB").save(output, format="JPEG", quality=91, optimize=True)
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
