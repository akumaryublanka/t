"""MikasaVerse anime-themed music player artwork.

Builds a 1280x720 Telegram-ready player image. The left panel is a custom
anime-inspired Mikasa portrait; the actual song cover is shown as a smaller
secondary artwork tile so branding stays consistent across every track.
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Optional, Union
from urllib.request import Request, urlopen
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


ImageSource = Union[str, Path, bytes, bytearray, BinaryIO, Image.Image]
WIDTH, HEIGHT = 1280, 720
DEFAULT_BRAND = "MIKASAVERSE"
DEFAULT_ACCENT = "#9A83FF"
PURPLE = (154, 131, 255)
CRIMSON = (224, 58, 88)
DARK = (7, 9, 19)


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
            request = Request(
                source,
                headers={"User-Agent": "Mozilla/5.0 MikasaVerse/1.0"},
            )
            with urlopen(request, timeout=8) as response:
                data = response.read(12 * 1024 * 1024 + 1)
            if len(data) > 12 * 1024 * 1024:
                return None
            image = Image.open(BytesIO(data))
        else:
            image = Image.open(source)
        image = ImageOps.exif_transpose(image)
        image.load()
        if image.width < 48 or image.height < 48:
            return None
        return image.convert("RGB")
    except Exception:
        return None


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(
        image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.42)
    )


def _width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, max_width: int,
    preferred: int, minimum: int = 18,
):
    size = preferred
    while size > minimum:
        font = _font(size, True)
        if _width(draw, text, font) <= max_width:
            return font
        size -= 2
    return _font(minimum, True)


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font,
    max_width: int, max_lines: int = 2,
) -> list[str]:
    words = str(text or "Unknown Track").split() or ["Unknown Track"]
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
        return str(value or "LIVE / UNKNOWN")
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _gradient(size: tuple[int, int], top: tuple[int, int, int],
              bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return image


def _anime_portrait(size: tuple[int, int], accent: tuple[int, int, int]) -> Image.Image:
    """Draw a clean anime-inspired heroine with dark hair and Mikasa-red scarf."""
    w, h = size
    base = _gradient(size, (23, 23, 49), (8, 10, 22)).convert("RGBA")
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((w * .48, -h * .12, w * 1.2, h * .68), fill=(*accent, 75))
    gd.ellipse((-w * .35, h * .48, w * .6, h * 1.18), fill=(*CRIMSON, 45))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(18, w // 10))))
    d = ImageDraw.Draw(base, "RGBA")

    # Distant rain, ember flecks and speed lines.
    for i in range(17):
        x = (i * 47 + 23) % w
        y = (i * 83 + 11) % h
        d.line((x, y, x - 12, y + 48), fill=(175, 166, 255, 35), width=2)
    for i in range(19):
        x = (i * 73 + 31) % w
        y = (i * 59 + 17) % h
        d.ellipse((x, y, x + 3, y + 3), fill=(255, 108, 132, 125))

    # Jacket / shoulders.
    d.polygon(
        [(18, h), (42, h * .82), (115, h * .72), (w * .5, h * .78),
         (w - 35, h * .8), (w - 8, h),],
        fill=(22, 27, 43, 255),
    )
    d.polygon(
        [(75, h), (122, h * .82), (w * .5, h * .88), (w - 96, h),
         (w - 155, h * .76), (w * .52, h * .81), (170, h * .75)],
        fill=(42, 49, 72, 255),
    )
    # Back hair mass, ears and neck.
    d.ellipse((w * .19, h * .07, w * .83, h * .75), fill=(8, 10, 20, 255))
    d.rounded_rectangle((w * .43, h * .58, w * .59, h * .78),
                        radius=18, fill=(207, 151, 146, 255))
    d.ellipse((w * .30, h * .38, w * .42, h * .57), fill=(210, 155, 149, 255))
    d.ellipse((w * .60, h * .38, w * .72, h * .57), fill=(210, 155, 149, 255))
    # Face, with slight shadow plane.
    d.ellipse((w * .34, h * .22, w * .67, h * .68), fill=(237, 190, 178, 255))
    d.polygon(
        [(w * .34, h * .43), (w * .40, h * .58), (w * .50, h * .69),
         (w * .66, h * .53), (w * .62, h * .67), (w * .51, h * .74),
         (w * .39, h * .65)],
        fill=(174, 112, 121, 95),
    )
    # Hair framing the cheeks and layered fringe.
    d.polygon(
        [(w * .28, h * .36), (w * .27, h * .16), (w * .36, h * .07),
         (w * .50, h * .03), (w * .69, h * .11), (w * .78, h * .30),
         (w * .72, h * .60), (w * .66, h * .72), (w * .68, h * .44),
         (w * .63, h * .30), (w * .53, h * .35), (w * .46, h * .28),
         (w * .38, h * .39), (w * .34, h * .64)],
        fill=(7, 9, 18, 255),
    )
    d.polygon(
        [(w * .34, h * .29), (w * .39, h * .08), (w * .49, h * .16),
         (w * .51, h * .32), (w * .45, h * .42), (w * .44, h * .26)],
        fill=(13, 15, 29, 255),
    )
    d.polygon(
        [(w * .48, h * .32), (w * .58, h * .11), (w * .62, h * .17),
         (w * .57, h * .38), (w * .53, h * .44)],
        fill=(11, 13, 25, 255),
    )
    d.polygon(
        [(w * .60, h * .30), (w * .70, h * .16), (w * .72, h * .43),
         (w * .66, h * .51), (w * .64, h * .38)],
        fill=(8, 10, 20, 255),
    )
    # Eyes and brows: sharp, restrained anime linework.
    for cx in (w * .425, w * .585):
        d.line((cx - 22, h * .445, cx + 20, h * .438),
               fill=(47, 34, 48, 255), width=5)
        d.ellipse((cx - 18, h * .45, cx + 18, h * .49),
                  fill=(246, 231, 229, 255))
        d.ellipse((cx - 7, h * .449, cx + 8, h * .50),
                  fill=(106, 65, 77, 255))
        d.ellipse((cx - 2, h * .453, cx + 4, h * .468),
                  fill=(255, 255, 255, 245))
        d.line((cx - 23, h * .425, cx + 18, h * .414),
               fill=(22, 18, 31, 255), width=4)
    d.line((w * .51, h * .49, w * .495, h * .56),
           fill=(157, 104, 111, 190), width=3)
    d.line((w * .475, h * .61, w * .535, h * .615),
           fill=(115, 49, 65, 220), width=3)

    # Signature crimson scarf wrapping across the lower portrait.
    d.polygon(
        [(w * .20, h * .67), (w * .36, h * .63), (w * .54, h * .69),
         (w * .77, h * .65), (w * .90, h * .75), (w * .70, h * .82),
         (w * .46, h * .77), (w * .28, h * .82)],
        fill=(111, 20, 43, 255),
    )
    d.polygon(
        [(w * .22, h * .70), (w * .38, h * .68), (w * .57, h * .73),
         (w * .78, h * .70), (w * .83, h * .75), (w * .59, h * .78),
         (w * .40, h * .74), (w * .25, h * .77)],
        fill=(202, 43, 73, 255),
    )
    d.line((w * .27, h * .73, w * .76, h * .76),
           fill=(255, 132, 150, 135), width=3)
    # Jacket lapels and small wing-inspired insignia.
    d.polygon([(w * .40, h * .80), (w * .50, h * .91),
               (w * .44, h), (w * .32, h * .83)], fill=(72, 84, 112, 255))
    d.polygon([(w * .60, h * .80), (w * .52, h * .92),
               (w * .63, h), (w * .74, h * .83)], fill=(48, 57, 83, 255))
    d.polygon([(w * .72, h * .88), (w * .77, h * .84), (w * .75, h * .91),
               (w * .81, h * .89), (w * .77, h * .96), (w * .73, h * .94)],
              fill=(199, 208, 229, 160))
    # Fine highlights in the hair.
    d.arc((w * .29, h * .07, w * .73, h * .50), 195, 315,
          fill=(111, 89, 160, 150), width=3)
    d.line((w * .33, h * .18, w * .40, h * .40),
           fill=(110, 94, 154, 100), width=2)
    return base.convert("RGB")


def _load_mikasa_portrait(size: tuple[int, int], accent: tuple[int, int, int]) -> Image.Image:
    """Load the bundled Mikasa-inspired portrait; use vector fallback if unavailable."""
    asset = Path(__file__).parent / "assets" / "mikasa_music_art.jpg.b64"
    try:
        encoded = asset.read_text(encoding="ascii").strip()
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(raw)) as image:
            image.load()
            return _cover(image.convert("RGB"), size)
    except Exception:
        return _anime_portrait(size, accent)


def _draw_equalizer(draw: ImageDraw.ImageDraw, x: int, y: int,
                    accent: tuple[int, int, int]) -> None:
    for i in range(28):
        height = 10 + int((math.sin(i * .82) + 1) * 14)
        color = (*accent, 205 if i % 4 else 255)
        draw.rounded_rectangle(
            (x + i * 12, y - height // 2, x + i * 12 + 5, y + height // 2),
            radius=3, fill=color,
        )


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
    """Render a branded anime-style 1280x720 music player card."""
    title = str(title or "Unknown Track").strip()
    artist = str(artist or "Unknown Artist").strip()
    brand = str(brand or DEFAULT_BRAND).strip().upper()
    accent = _rgb(accent_color)
    track_art = _load_artwork(artwork)

    # Deep ink gradient with colored light borrowed from the current song cover.
    if track_art:
        background = _cover(track_art, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(44))
        background = Image.blend(background, Image.new("RGB", (WIDTH, HEIGHT), DARK), .86)
        canvas = background.convert("RGBA")
    else:
        canvas = _gradient((WIDTH, HEIGHT), (16, 14, 34), DARK).convert("RGBA")
        glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((-280, 110, 550, 910), fill=(*accent, 70))
        gd.ellipse((850, -300, 1540, 470), fill=(*CRIMSON, 38))
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(130)))

    # Darken the right side for high-contrast track information.
    shade = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(WIDTH):
        alpha = int(28 + 94 * max(0, min(1, (x - 430) / 850)))
        sd.line((x, 0, x, HEIGHT), fill=(3, 5, 13, alpha))
    canvas.alpha_composite(shade)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Outer frame and left portrait panel.
    draw.rounded_rectangle((18, 18, WIDTH - 18, HEIGHT - 18),
                           radius=30, outline=(*accent, 185), width=2)
    panel = _load_mikasa_portrait((470, 570), accent)
    panel_mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(panel_mask).rounded_rectangle(
        (0, 0, panel.width - 1, panel.height - 1), radius=26, fill=255
    )
    shadow = Image.new("RGBA", (510, 610), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (18, 18, 492, 592), radius=32, fill=(0, 0, 0, 180)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)), (42, 55))
    canvas.paste(panel, (62, 72), panel_mask)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((62, 72, 532, 642), radius=26,
                           outline=(*accent, 230), width=3)
    draw.rounded_rectangle((83, 92, 240, 126), radius=16,
                           fill=(8, 10, 20, 190), outline=(*accent, 130), width=1)
    draw.text((101, 101), "MIKASA // MUSIC", font=_font(13, True),
              fill=(231, 226, 255, 245))
    draw.rounded_rectangle((83, 580, 292, 616), radius=17,
                           fill=(107, 20, 43, 215), outline=(233, 76, 105, 170), width=1)
    draw.text((101, 590), "SCARLET EDITION", font=_font(13, True),
              fill=(255, 218, 226, 245))

    # Header, brand and live indicator.
    left, max_text = 590, 620
    draw.text((left, 78), "M I K A S A V E R S E",
              font=_font(19, True), fill=(238, 235, 255, 255))
    draw.text((left, 111), "ANIME MUSIC PLAYER  /  VOL. 01",
              font=_font(12, True), fill=(157, 151, 188, 220))
    _draw_equalizer(draw, 1004, 94, accent)
    draw.rounded_rectangle((left, 156, left + 168, 194), radius=19,
                           fill=(31, 175, 134, 50), outline=(68, 228, 178, 170), width=1)
    draw.ellipse((left + 15, 170, left + 26, 181), fill=(78, 244, 181, 255))
    draw.text((left + 38, 165), "NOW PLAYING", font=_font(14, True),
              fill=(174, 255, 225, 255))

    # Title and artist are kept separate so long metadata does not collide.
    title_font = _fit_font(draw, title, max_text, 45, 26)
    y = 235
    for line in _wrap(draw, title, title_font, max_text, max_lines=2):
        draw.text((left + 2, y + 3), line, font=title_font, fill=(0, 0, 0, 175))
        draw.text((left, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += title_font.size + 15
    artist_y = max(y + 16, 355)
    artist_font = _fit_font(draw, artist, max_text, 26, 18)
    draw.text((left, artist_y), artist, font=artist_font,
              fill=(191, 185, 222, 250))
    draw.rounded_rectangle((left, artist_y + 50, left + max_text, artist_y + 52),
                           radius=1, fill=(255, 255, 255, 35))

    # Metadata cards.
    meta_y = artist_y + 82
    card_top, card_bottom = meta_y - 14, meta_y + 82
    draw.rounded_rectangle((left, card_top, left + 210, card_bottom), radius=17,
                           fill=(12, 15, 31, 180), outline=(153, 133, 255, 75), width=1)
    draw.rounded_rectangle((left + 226, card_top, left + 500, card_bottom), radius=17,
                           fill=(12, 15, 31, 180), outline=(153, 133, 255, 75), width=1)
    draw.text((left + 18, meta_y), "DURATION", font=_font(12, True),
              fill=(145, 143, 179, 235))
    draw.text((left + 18, meta_y + 26), _time_label(duration), font=_font(23, True),
              fill=(248, 247, 255, 255))
    draw.text((left + 244, meta_y), "REQUESTED BY", font=_font(12, True),
              fill=(145, 143, 179, 235))
    req = str(requester or "Unknown").strip()
    req_font = _fit_font(draw, req, 232, 21, 15)
    draw.text((left + 244, meta_y + 27), req, font=req_font,
              fill=(248, 247, 255, 255))

    # Small original song-cover tile. The Mikasa portrait stays the primary art.
    if track_art:
        tile = _cover(track_art, (76, 76)).convert("RGBA")
        tile_mask = Image.new("L", (76, 76), 0)
        ImageDraw.Draw(tile_mask).rounded_rectangle((0, 0, 75, 75), radius=13, fill=255)
        tile_x, tile_y = 1114, 488
        canvas.paste(tile, (tile_x, tile_y), tile_mask)
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle((tile_x, tile_y, tile_x + 76, tile_y + 76),
                               radius=13, outline=(*accent, 220), width=2)
        draw.text((tile_x - 18, tile_y + 83), "TRACK ART", font=_font(10, True),
                  fill=(156, 149, 190, 225))
    else:
        # A subtle vinyl mark replaces the old oversized generic music-note placeholder.
        cx, cy = 1152, 526
        draw.ellipse((cx - 38, cy - 38, cx + 38, cy + 38),
                     outline=(*accent, 150), width=2)
        draw.ellipse((cx - 23, cy - 23, cx + 23, cy + 23),
                     outline=(255, 255, 255, 80), width=2)
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5),
                     fill=(*CRIMSON, 230))

    # Progress rail and animated-looking equalizer accents.
    bar_x, bar_y, bar_w = left, 585, 475
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + 7),
                           radius=4, fill=(255, 255, 255, 55))
    total, elapsed = _seconds(duration), _seconds(position)
    progress = min(1.0, elapsed / total) if total and elapsed is not None else 0.0
    filled = int(bar_w * progress)
    if filled:
        draw.rounded_rectangle((bar_x, bar_y, bar_x + filled, bar_y + 7),
                               radius=4, fill=(*accent, 255))
    draw.ellipse((bar_x + filled - 5, bar_y - 4, bar_x + filled + 7, bar_y + 8),
                 fill=(255, 255, 255, 245))
    for i in range(18):
        height = 5 + int((math.sin(i * .91) + 1) * 5)
        x = left + 3 + i * 17
        draw.rounded_rectangle((x, 630 - height, x + 4, 630 + height),
                               radius=2, fill=(*CRIMSON, 115 if i % 3 else 210))

    draw.line((62, 672, WIDTH - 62, 672), fill=(255, 255, 255, 40), width=1)
    draw.text((64, 682), "✦  MIKASA-INSPIRED  •  PREMIUM MUSIC EXPERIENCE",
              font=_font(12, True), fill=(158, 160, 190, 235))
    brand_font = _font(14, True)
    brand_width = _width(draw, brand, brand_font)
    draw.text((WIDTH - 66 - brand_width, 680), brand, font=brand_font,
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


# ---------------------------------------------------------------------------
# Sakura Moon player theme (uses the uploaded Mikasa artwork as its backdrop)
# ---------------------------------------------------------------------------
_LEGACY_CREATE_MUSIC_THUMBNAIL = create_music_thumbnail


def _sakura_font(size: int, *, italic: bool = False, bold: bool = False):
    candidates = []
    if italic:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Italic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
            "C:/Windows/Fonts/georgiai.ttf",
        ])
    elif bold:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ])
    else:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
            "C:/Windows/Fonts/georgia.ttf",
        ])
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return _font(size, bold)


def _load_sakura_template() -> Optional[Image.Image]:
    path = Path(__file__).parent / "assets" / "mikasa_sakura_player.jpg"
    try:
        with Image.open(path) as image:
            image.load()
            return ImageOps.fit(
                image.convert("RGB"), (WIDTH, HEIGHT),
                method=Image.Resampling.LANCZOS, centering=(0.5, 0.5),
            )
    except Exception:
        return None


def create_music_thumbnail(
    title: str,
    artist: str = "Unknown Artist",
    artwork: Optional[ImageSource] = None,
    *,
    duration=None,
    requester: Optional[str] = None,
    brand: str = DEFAULT_BRAND,
    accent_color: str = "#EE538B",
    position=None,
    output: Optional[Union[str, Path]] = None,
) -> BytesIO:
    """Render uploaded Sakura-Mikasa artwork with live track metadata."""
    canvas_image = _load_sakura_template()
    if canvas_image is None:
        return _LEGACY_CREATE_MUSIC_THUMBNAIL(
            title, artist, artwork, duration=duration, requester=requester,
            brand=brand, accent_color=accent_color, position=position, output=output,
        )

    title = str(title or "Unknown Track").strip()
    artist = str(artist or "Unknown Artist").strip()
    requester = str(requester or "Unknown").strip()
    brand = str(brand or DEFAULT_BRAND).strip().upper()
    duration_text = _time_label(duration)
    accent = _rgb(accent_color)
    pale = (250, 239, 244, 255)
    muted = (205, 190, 199, 255)
    pink = (*accent, 255)
    canvas = canvas_image.convert("RGBA")

    # Mask the example title/player information printed into the uploaded artwork.
    shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (648, 217, 1260, 661), radius=24, fill=(0, 0, 0, 210)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))

    panel = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        (650, 220, 1254, 656), radius=22,
        fill=(5, 5, 9, 248), outline=(*accent, 115), width=1,
    )
    panel_draw.line((682, 221, 1222, 221), fill=(*accent, 80), width=1)
    canvas.alpha_composite(panel)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Restrained eyebrow and equalizer accent.
    draw.rounded_rectangle((686, 251, 728, 254), radius=2, fill=pink)
    draw.text((740, 239), "NOW PLAYING", font=_sakura_font(12, bold=True),
              fill=(241, 145, 177, 255))
    heights = [8, 14, 22, 13, 30, 17, 10, 24, 34, 18, 12, 26, 15, 9, 22, 30, 13, 18, 8]
    for index, height in enumerate(heights):
        x = 1080 + index * 4
        draw.rounded_rectangle(
            (x, 256 - height, x + 2, 256), radius=1,
            fill=(*accent, 205 if index % 3 else 255),
        )

    title_font = _sakura_font(57, italic=True)
    while _width(draw, title, title_font) > 530 and title_font.size > 39:
        title_font = _sakura_font(title_font.size - 2, italic=True)
    title_lines = _wrap(draw, title, title_font, 530, max_lines=2)
    if len(title_lines) > 1:
        title_font = _sakura_font(min(title_font.size, 46), italic=True)
        title_lines = _wrap(draw, title, title_font, 530, max_lines=2)

    title_y = 281
    title_line_gap = title_font.size + 7
    for line in title_lines:
        draw.text((682, title_y), line, font=title_font, fill=pale)
        title_y += title_line_gap
    draw.line((686, title_y + 1, 1220, title_y + 1),
              fill=(*accent, 145), width=1)

    artist_font = _sakura_font(17)
    artist_lines = _wrap(draw, artist, artist_font, 520, max_lines=2)
    artist_y = title_y + 16
    for line in artist_lines:
        draw.text((688, artist_y), line, font=artist_font, fill=muted)
        artist_y += 23

    meta_y = max(artist_y + 14, 457)
    meta_y = min(meta_y, 498)
    draw.line((686, meta_y - 13, 1218, meta_y - 13),
              fill=(255, 255, 255, 58), width=1)
    draw.text((688, meta_y), "DURATION", font=_sakura_font(10, bold=True),
              fill=(177, 151, 166, 255))
    draw.text((688, meta_y + 19), duration_text,
              font=_sakura_font(22, bold=True), fill=pale)
    draw.line((842, meta_y + 1, 842, meta_y + 47),
              fill=(255, 255, 255, 70), width=1)
    draw.text((865, meta_y), "REQUESTED BY", font=_sakura_font(10, bold=True),
              fill=(177, 151, 166, 255))
    request_font = _sakura_font(20, bold=True)
    while _width(draw, requester, request_font) > 320 and request_font.size > 14:
        request_font = _sakura_font(request_font.size - 1, bold=True)
    draw.text((865, meta_y + 18), requester, font=request_font, fill=pale)

    # Decorative rail only; never pretend to know an elapsed playback position.
    rail_y = 574
    draw.rounded_rectangle((688, rail_y, 1190, rail_y + 3), radius=2,
                           fill=(255, 255, 255, 50))
    draw.rounded_rectangle((688, rail_y, 825, rail_y + 3), radius=2,
                           fill=(*accent, 225))
    draw.text((688, 588), f"{brand}  /  LISTEN · ENJOY · VIBE",
              font=_sakura_font(9, bold=True), fill=(164, 143, 154, 245))

    # Recreate the vertical Japanese signature as a small accent on the panel edge.
    for jp_path in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            jp_font = ImageFont.truetype(jp_path, 18)
            draw.text((1219, 548), "進", font=jp_font, fill=pink)
            draw.text((1219, 575), "撃", font=jp_font, fill=pink)
            break
        except OSError:
            continue

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


# If the uploaded background asset is unavailable, compose the Sakura-night scene
# from the repository's bundled Mikasa portrait so the player never falls back
# to an unrelated generic card.
def _load_sakura_template() -> Optional[Image.Image]:
    accent = (238, 83, 139)
    try:
        base = Image.new("RGBA", (WIDTH, HEIGHT), (5, 5, 11, 255))

        # Deep charcoal-to-plum night gradient.
        gradient = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gradient)
        for x in range(WIDTH):
            t = x / max(WIDTH - 1, 1)
            color = (int(16 - 10 * t), int(8 - 3 * t), int(29 - 15 * t), 255)
            gd.line((x, 0, x, HEIGHT), fill=color)
        base.alpha_composite(gradient)

        # Moon glow and scattered stars, kept behind the portrait.
        glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((470, -100, 800, 230), fill=(255, 177, 204, 70))
        glow_draw.ellipse((540, -35, 730, 155), fill=(255, 224, 231, 190))
        glow = glow.filter(ImageFilter.GaussianBlur(28))
        base.alpha_composite(glow)

        # Use the existing bundled hand-drawn Mikasa artwork from the repository.
        portrait = _load_mikasa_portrait((690, HEIGHT), accent).convert("RGBA")
        base.alpha_composite(portrait, (0, 0))

        # Fade the portrait into the dark information area.
        fade = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fade)
        for x in range(545, 735):
            alpha = int(255 * max(0.0, min(1.0, (x - 545) / 190)))
            fd.line((x, 0, x, HEIGHT), fill=(5, 5, 11, alpha))
        base.alpha_composite(fade)

        draw = ImageDraw.Draw(base, "RGBA")

        # Soft cherry-blossom clusters and drifting petals.
        petals = [
            (20, 22, 13, 6), (50, 42, 10, 5), (93, 15, 9, 5),
            (445, 28, 10, 5), (505, 110, 8, 4), (120, 625, 12, 5),
            (500, 655, 10, 5), (585, 190, 8, 4), (620, 60, 7, 4),
        ]
        for px, py, rx, ry in petals:
            draw.ellipse((px-rx, py-ry, px+rx, py+ry),
                         fill=(242, 106, 157, 125))
        for px, py in ((300, 80), (385, 165), (75, 285), (560, 405), (180, 590)):
            draw.ellipse((px-8, py-3, px+8, py+3),
                         fill=(255, 168, 196, 155))

        # Elegant corner branding and Mikasa quote, matching the reference mood.
        draw.line((42, 42, 42, 112), fill=(245, 112, 164, 235), width=2)
        draw.text((60, 42), "MIKASAVERSE",
                  font=_sakura_font(22, italic=True), fill=(249, 235, 241, 255))
        draw.text((61, 73), "M U S I C   P L A Y E R",
                  font=_sakura_font(9, bold=True), fill=(187, 166, 180, 255))

        draw.line((835, 48, 1222, 48), fill=(245, 220, 231, 60), width=1)
        draw.text((1005, 65), "“Even if I die…",
                  font=_sakura_font(18, italic=True), fill=(229, 214, 224, 255))
        draw.text((850, 94), "I want to be by your side.”",
                  font=_sakura_font(18, italic=True), fill=(229, 214, 224, 255))
        draw.text((1115, 125), "— Mikasa",
                  font=_sakura_font(15, italic=True), fill=(210, 190, 202, 255))

        draw.line((42, 650, 42, 690), fill=(245, 112, 164, 235), width=2)
        draw.text((60, 646), "M I K A S A V E R S E",
                  font=_sakura_font(17), fill=(225, 207, 217, 255))
        draw.text((61, 674), "L I S T E N   ·   E N J O Y   ·   V I B E",
                  font=_sakura_font(8, bold=True), fill=(172, 153, 166, 255))
        return base.convert("RGB")
    except Exception:
        return _load_mikasa_portrait((WIDTH, HEIGHT), accent).convert("RGB")
