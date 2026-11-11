"""Premium 1280x720 thumbnails for MikasaVerse music playback.

Install: Pillow is already included in the project requirements.

Example:
    from waifu.music.thumbnail import create_music_thumbnail

    image = create_music_thumbnail(
        title=track["title"],
        artist=track.get("artist") or track.get("uploader"),
        artwork=track.get("thumbnail"),
        duration=track.get("duration"),
        requester=track.get("requested_by"),
    )
    await message.reply_photo(photo=image)

The generator returns a seeked BytesIO PNG. Artwork failures are non-fatal.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Optional, Union
from urllib.request import Request, urlopen
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


ImageSource = Union[str, Path, bytes, bytearray, BinaryIO, Image.Image]

WIDTH = 1280
HEIGHT = 720
DEFAULT_BRAND = "MIKASAVERSE"
DEFAULT_ACCENT = "#8B7CFF"
DARK = (8, 10, 20)


def _font(size: int, bold: bool = False):
    paths = (
        ("C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")
        if bold else
        ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")
    )
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError("accent_color must be in #RRGGBB format")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _load_artwork(source: Optional[ImageSource]) -> Optional[Image.Image]:
    if source is None:
        return None
    try:
        if isinstance(source, Image.Image):
            image = source.copy()
        elif isinstance(source, (bytes, bytearray)):
            image = Image.open(BytesIO(source))
        elif isinstance(source, str) and source.startswith(("http://", "https://")):
            request = Request(source, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=12) as response:
                data = response.read(15 * 1024 * 1024 + 1)
            if len(data) > 15 * 1024 * 1024:
                return None
            image = Image.open(BytesIO(data))
        else:
            image = Image.open(source)
        image = ImageOps.exif_transpose(image)
        image.load()
        return image.convert("RGB")
    except Exception:
        return None


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int,
              preferred: int, minimum: int = 22):
    size = preferred
    while size > minimum:
        font = _font(size, True)
        if _width(draw, text, font) <= max_width:
            return font
        size -= 2
    return _font(minimum, True)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int,
          max_lines: int = 3) -> list[str]:
    words = text.split() or ["Unknown Track"]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _width(draw, last + "…", font) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = (last or "") + "…"
    return lines


def _seconds(value) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        if ":" not in text:
            return max(0, int(float(text)))
        total = 0
        for part in text.split(":"):
            total = total * 60 + int(part)
        return max(0, total)
    except (TypeError, ValueError):
        return None


def _time_label(value) -> str:
    seconds = _seconds(value)
    if seconds is None:
        return str(value or "Live / unknown")
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def create_music_thumbnail(
    title: str,
    artist: str = "Unknown Artist",
    artwork: Optional[ImageSource] = None,
    *,
    duration=None,
    requester: Optional[str] = None,
    brand: str = DEFAULT_BRAND,
    accent_color: str = DEFAULT_ACCENT,
    position=None,
    output: Optional[Union[str, Path]] = None,
) -> BytesIO:
    """Render a premium music card and return it as a Telegram-ready PNG.

    artwork accepts an HTTP(S) URL, local path, bytes, file object, or PIL Image.
    Missing or inaccessible artwork automatically gets a designed fallback.
    """
    title = str(title or "Unknown Track").strip()
    artist = str(artist or "Unknown Artist").strip()
    brand = str(brand or DEFAULT_BRAND).strip().upper()
    accent = _rgb(accent_color)

    art = _load_artwork(artwork)
    if art:
        bg = _cover(art, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(38))
        bg = Image.blend(bg, Image.new("RGB", (WIDTH, HEIGHT), DARK), 0.78)
        canvas = bg.convert("RGBA")
    else:
        canvas = Image.new("RGBA", (WIDTH, HEIGHT), DARK)
        glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((700, -300, 1450, 470), fill=(*accent, 75))
        gd.ellipse((-300, 430, 450, 1050), fill=(34, 190, 180, 55))
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(100)))

    tint = Image.new("RGBA", (WIDTH, HEIGHT), (4, 6, 15, 100))
    canvas.alpha_composite(tint)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Premium frame and ambient equalizer detail.
    draw.rounded_rectangle((18, 18, WIDTH - 18, HEIGHT - 18), radius=30,
                           outline=(*accent, 150), width=2)
    for i in range(30):
        h = 8 + int((math.sin(i * 0.8) + 1) * 13)
        x = 810 + i * 12
        draw.rounded_rectangle((x, 82 - h // 2, x + 4, 82 + h // 2),
                               radius=2, fill=(*accent, 80))

    # Album artwork, shadow, rounded corners.
    art_size, art_x, art_y = 440, 72, 135
    shadow = Image.new("RGBA", (art_size + 56, art_size + 56), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((20, 20, art_size + 36, art_size + 36),
                                             radius=34, fill=(0, 0, 0, 190))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(20)),
                           (art_x - 28, art_y - 28))
    if art:
        cover = _cover(art, (art_size, art_size)).convert("RGBA")
    else:
        cover = Image.new("RGBA", (art_size, art_size), (20, 24, 44, 255))
        cd = ImageDraw.Draw(cover)
        cd.ellipse((58, 58, 382, 382), outline=(*accent, 210), width=5)
        cd.ellipse((112, 112, 328, 328), outline=(255, 255, 255, 65), width=3)
        cd.ellipse((188, 188, 252, 252), fill=(*accent, 235))
        cd.text((150, 135), "♫", font=_font(100, True), fill=(255, 255, 255, 235))
    mask = Image.new("L", (art_size, art_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, art_size - 1, art_size - 1),
                                           radius=28, fill=255)
    canvas.paste(cover, (art_x, art_y), mask)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((art_x, art_y, art_x + art_size, art_y + art_size),
                           radius=28, outline=(*accent, 220), width=3)

    # Track information.
    left, max_text = 575, 625
    draw.text((left, 133), brand, font=_font(18, True), fill=(225, 230, 248, 235))
    draw.rounded_rectangle((left + 365, 126, left + 505, 160), radius=17,
                           fill=(30, 180, 140, 45), outline=(65, 225, 175, 160), width=1)
    draw.ellipse((left + 380, 138, left + 390, 148), fill=(70, 235, 175, 255))
    draw.text((left + 400, 133), "NOW PLAYING", font=_font(13, True),
              fill=(165, 255, 220, 255))

    title_font = _fit_font(draw, title, max_text, 50, 26)
    y = 220
    for line in _wrap(draw, title, title_font, max_text):
        draw.text((left + 2, y + 3), line, font=title_font, fill=(0, 0, 0, 150))
        draw.text((left, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += title_font.size + 14

    artist_y = max(y + 10, 365)
    artist_font = _fit_font(draw, artist, max_text, 26, 18)
    draw.text((left, artist_y), artist, font=artist_font, fill=(180, 190, 215, 245))

    meta_y = artist_y + 62
    draw.text((left, meta_y), "DURATION", font=_font(12, True),
              fill=(130, 145, 175, 235))
    draw.text((left, meta_y + 21), _time_label(duration), font=_font(18),
              fill=(240, 243, 255, 250))
    if requester:
        req_x = left + 205
        draw.text((req_x, meta_y), "REQUESTED BY", font=_font(12, True),
                  fill=(130, 145, 175, 235))
        req_text = str(requester).strip()
        req_font = _fit_font(draw, req_text, 410, 18, 14)
        draw.text((req_x, meta_y + 21), req_text, font=req_font,
                  fill=(240, 243, 255, 250))

    # Playback timeline. Without a position, show an idle track line.
    bar_x, bar_y, bar_w = left, 560, max_text
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + 7),
                           radius=4, fill=(255, 255, 255, 55))
    total = _seconds(duration)
    elapsed = _seconds(position)
    progress = min(1.0, elapsed / total) if total and elapsed is not None else 0.0
    filled = int(bar_w * progress)
    if filled:
        draw.rounded_rectangle((bar_x, bar_y, bar_x + filled, bar_y + 7),
                               radius=4, fill=(*accent, 255))
    knob_x = bar_x + filled
    draw.ellipse((knob_x - 6, bar_y - 5, knob_x + 6, bar_y + 7),
                 fill=(255, 255, 255, 245))

    draw.line((72, 610, WIDTH - 72, 610), fill=(255, 255, 255, 40), width=1)
    footer = _font(15, True)
    draw.text((72, 637), "♪  PREMIUM MUSIC EXPERIENCE", font=footer,
              fill=(160, 172, 198, 225))
    brand_w = _width(draw, brand, footer)
    draw.text((WIDTH - 72 - brand_w, 637), brand, font=footer,
              fill=(*accent, 245))

    result = BytesIO()
    canvas.convert("RGB").save(result, format="PNG", optimize=True)
    result.seek(0)
    if output is not None:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.getvalue())
        result.seek(0)
    return result


generate_thumbnail = create_music_thumbnail
