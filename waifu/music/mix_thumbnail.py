"""Premium anime-neon MIKASAVERSE related-song recommendation poster."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from waifu.music.thumbnail import (
    _cover,
    _font,
    _load_artwork,
    _load_mikasa_portrait,
    _time_label,
)

W, H = 960, 1440
INK = (13, 7, 31)
PINK = (244, 94, 207)
PINK_LIGHT = (255, 174, 230)
PURPLE = (155, 100, 255)
CYAN = (112, 213, 255)
WHITE = (250, 245, 255)
MUTED = (185, 169, 211)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int,
         size: int = 20, minimum: int = 12, *, bold: bool = True):
    text = str(text or "Unknown")
    while size > minimum:
        font = _font(size, bold)
        if _text_width(draw, text, font) <= max_width:
            return font
        size -= 1
    return _font(minimum, bold)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font,
          max_width: int, max_lines: int = 2) -> list[str]:
    words = str(text or "Unknown track").split() or ["Unknown track"]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = (last.rstrip() + "…") if last else "…"
    return lines


def _rounded_cover(canvas: Image.Image, cover: Optional[Image.Image],
                   xy: tuple[int, int], size: tuple[int, int],
                   border=PINK) -> None:
    x, y = xy
    if cover is None:
        cover = Image.new("RGB", size, (48, 24, 78))
        cd = ImageDraw.Draw(cover)
        cd.ellipse((size[0]//3, size[1]//6, size[0]*2//3, size[1]*5//6),
                   fill=(103, 51, 145))
        cd.text((size[0]//2-15, size[1]//2-28), "♫",
                font=_font(40, True), fill=WHITE)
    cover = _cover(cover.convert("RGB"), size).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0]-1, size[1]-1), radius=13, fill=255
    )
    shadow = Image.new("RGBA", (size[0]+18, size[1]+18), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (5, 5, size[0]+12, size[1]+12), radius=17, fill=(0, 0, 0, 145)
    )
    canvas.alpha_composite(shadow, (x-9, y-9))
    canvas.paste(cover, (x, y), mask)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(
        (x-4, y-4, x+size[0]+4, y+size[1]+4),
        radius=16, outline=border, width=3
    )


def _pill(draw: ImageDraw.ImageDraw, xy, text: str, *,
          fill, outline, color=WHITE, font_size=13):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=(y2-y1)//2, fill=fill, outline=outline, width=2)
    font = _font(font_size, True)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    draw.text((x1+(x2-x1-tw)//2, y1+(y2-y1-th)//2-bbox[1]),
              text, font=font, fill=color)


def _draw_blossom(draw: ImageDraw.ImageDraw, cx: int, cy: int,
                  radius: int = 8, alpha: int = 215) -> None:
    for angle in range(0, 360, 72):
        x = cx + int(math.cos(math.radians(angle))*radius)
        y = cy + int(math.sin(math.radians(angle))*radius)
        draw.ellipse((x-5, y-7, x+5, y+7), fill=(*PINK, alpha))
    draw.ellipse((cx-3, cy-3, cx+3, cy+3), fill=(255, 220, 139, alpha))


def _preload_covers(tracks: list[dict], current_track: dict) -> list[Optional[Image.Image]]:
    sources = [track.get("thumbnail") for track in tracks]
    sources.append(current_track.get("thumbnail"))
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(sources)))) as pool:
        return list(pool.map(_load_artwork, sources))


def create_mix_thumbnail(
    tracks: list[dict],
    current_track: Optional[dict] = None,
    *,
    brand: str = "MIKASAVERSE",
) -> BytesIO:
    """Create a premium 2:3 anime music-discovery poster with five song cards."""
    tracks = list(tracks or [])[:5]
    current_track = current_track or {}
    covers = _preload_covers(tracks, current_track)

    # Deep plum vertical gradient, dual neon glow and fine background texture.
    canvas = Image.new("RGBA", (W, H), (*INK, 255))
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        t = y / max(1, H-1)
        gd.line((0, y, W, y), fill=(int(20-7*t), int(8+t), int(47-15*t), 255))
    canvas.alpha_composite(grad)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((-210, -170, 530, 500), fill=(*PURPLE, 115))
    g.ellipse((410, -150, 1100, 490), fill=(*PINK, 70))
    g.ellipse((190, 860, 780, 1490), fill=(*CYAN, 23))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(115)))
    d = ImageDraw.Draw(canvas, "RGBA")

    for x in range(28, W, 32):
        d.line((x, 0, x, H), fill=(172, 131, 235, 17), width=1)
    for y in range(0, H, 32):
        d.line((0, y, W, y), fill=(172, 131, 235, 17), width=1)
    for x, y, r in [(58, 385, 3), (900, 370, 4), (910, 1050, 3),
                    (70, 1060, 3), (540, 365, 2), (858, 74, 3),
                    (70, 780, 2), (890, 790, 2)]:
        d.ellipse((x-r, y-r, x+r, y+r), fill=(*CYAN, 165))
    for x, y in [(56, 84), (105, 133), (395, 72), (420, 320),
                 (890, 115), (905, 310), (44, 1020), (914, 1025)]:
        _draw_blossom(d, x, y, 7, 155)

    d.rounded_rectangle((12, 12, W-12, H-12), radius=36,
                        fill=(15, 6, 37, 248), outline=(248, 232, 255, 255), width=7)
    d.rounded_rectangle((27, 27, W-27, H-27), radius=27,
                        outline=(*PURPLE, 230), width=3)

    # Cinematic anime portrait, softly framed.
    hero = _load_mikasa_portrait((400, 355), PINK).convert("RGBA")
    hero = ImageOps.fit(hero, (400, 355), method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.40))
    hero_mask = Image.new("L", (400, 355), 0)
    ImageDraw.Draw(hero_mask).rounded_rectangle((0, 0, 399, 354), radius=27, fill=255)
    hero_glow = Image.new("RGBA", (420, 375), (0, 0, 0, 0))
    ImageDraw.Draw(hero_glow).rounded_rectangle((8, 8, 412, 367), radius=34,
                                                outline=(*PINK, 200), width=6)
    canvas.alpha_composite(hero_glow.filter(ImageFilter.GaussianBlur(13)), (24, 34))
    canvas.paste(hero, (34, 44), hero_mask)
    d = ImageDraw.Draw(canvas, "RGBA")
    d.rounded_rectangle((30, 40, 438, 403), radius=31,
                        outline=(255, 224, 250, 235), width=4)
    _pill(d, (55, 58, 222, 92), "MIKASAVERSE", fill=(23, 8, 49, 220),
          outline=(*PINK, 190), color=WHITE, font_size=12)

    # Clear typographic hierarchy in the hero title panel.
    d.rounded_rectangle((455, 72, 920, 367), radius=28,
                        fill=(25, 9, 56, 238), outline=(*PURPLE, 230), width=3)
    d.rounded_rectangle((468, 85, 907, 354), radius=21,
                        outline=(*PINK, 110), width=1)
    _pill(d, (493, 105, 793, 141), "PERSONALISED RADIO",
          fill=(69, 27, 105, 230), outline=(*CYAN, 155),
          color=CYAN, font_size=13)
    d.text((490, 163), "RELATED", font=_font(48, True), fill=WHITE,
           stroke_width=1, stroke_fill=(*PURPLE, 255))
    d.text((488, 220), "SUGGESTIONS",
           font=_fit(d, "SUGGESTIONS", 395, 42, 31), fill=(*PINK, 255),
           stroke_width=1, stroke_fill=(116, 37, 145, 255))
    d.line((493, 280, 875, 280), fill=(*PURPLE, 170), width=2)
    d.text((494, 298), "FRESH TRACKS  ·  YOUR VIBE",
           font=_font(14, True), fill=(210, 192, 234, 255))
    d.text((853, 103), "✦", font=_font(25, True), fill=(255, 210, 126, 255))
    _draw_blossom(d, 873, 323, 8, 215)

    # Five clean recommendation cards.
    d.text((48, 414), "MADE FOR YOUR MOOD", font=_font(16, True), fill=CYAN)
    d.text((48, 439), "TOP 5 TRACKS", font=_font(25, True), fill=WHITE)
    _pill(d, (775, 418, 908, 452), f"{len(tracks)} PICKS",
          fill=(61, 24, 98, 245), outline=(*PINK, 190),
          color=PINK_LIGHT, font_size=12)

    row_top, row_h, row_gap = 474, 116, 12
    for index, track in enumerate(tracks):
        y = row_top + index * (row_h + row_gap)
        d.rounded_rectangle((43, y+5, 919, y+row_h+5), radius=22,
                            fill=(0, 0, 0, 90))
        d.rounded_rectangle((39, y, 915, y+row_h), radius=22,
                            fill=(28, 10, 58, 248), outline=(*PURPLE, 230), width=3)
        d.rounded_rectangle((48, y+8, 906, y+row_h-8), radius=16,
                            outline=(*PINK, 95), width=1)
        d.rounded_rectangle((57, y+17, 128, y+99), radius=18,
                            fill=(49, 19, 91, 255), outline=(*PINK, 245), width=3)
        d.text((78, y+34), str(index+1), font=_font(39, True), fill=WHITE,
               stroke_width=1, stroke_fill=(*PURPLE, 255))
        _rounded_cover(canvas, covers[index], (150, y+13), (90, 90),
                       border=CYAN if index % 2 else PINK)
        d = ImageDraw.Draw(canvas, "RGBA")

        title = str(track.get("title") or "Unknown track").strip()
        artist = str(track.get("artist") or track.get("uploader") or "Unknown artist").strip()
        title_font = _font(18, True)
        title_lines = _wrap(d, title, title_font, 480, max_lines=2)
        ty = y + (24 if len(title_lines) == 1 else 17)
        for line in title_lines:
            d.text((264, ty), line, font=title_font, fill=WHITE)
            ty += 22
        d.text((265, y+78), "♫  " + artist,
               font=_fit(d, artist, 475, 14, 11, bold=False), fill=MUTED)
        _pill(d, (760, y+70, 838, y+97), _time_label(track.get("duration")),
              fill=(48, 20, 83, 245), outline=(*CYAN, 140),
              color=(224, 240, 255), font_size=11)
        d.rounded_rectangle((850, y+29, 897, y+79), radius=14,
                            fill=(*PURPLE, 255), outline=(*PINK, 255), width=3)
        d.text((862, y+36), "▶", font=_font(25, True), fill=WHITE)

    # Current track footer with cover, artist, requester and progress decoration.
    panel_y = 1123
    d.rounded_rectangle((40, panel_y, 920, 1354), radius=29,
                        fill=(18, 7, 44, 250), outline=(*PINK, 245), width=4)
    d.rounded_rectangle((55, panel_y+12, 905, 1340), radius=22,
                        outline=(*PURPLE, 145), width=1)
    _pill(d, (70, 1099, 300, 1146), "NOW PLAYING  ♫",
          fill=(*PINK, 255), outline=(255, 226, 250, 255),
          color=(35, 8, 48), font_size=17)
    _rounded_cover(canvas, covers[-1], (76, 1172), (132, 132), border=CYAN)
    d = ImageDraw.Draw(canvas, "RGBA")

    current_title = str(current_track.get("title") or "Music is playing").strip()
    current_artist = str(current_track.get("artist") or current_track.get("uploader") or "MIKASAVERSE").strip()
    requester = str(current_track.get("requested_by") or "MIKASAVERSE listener").strip()
    d.text((235, 1178), current_title, font=_fit(d, current_title, 440, 20, 13), fill=WHITE)
    d.text((235, 1212), current_artist,
           font=_fit(d, current_artist, 420, 15, 11, bold=False), fill=MUTED)
    request_line = "REQUESTED BY  ·  " + requester
    d.text((235, 1241), request_line,
           font=_fit(d, request_line, 430, 12, 10), fill=(168, 149, 202, 255))
    # Decorative only: do not claim an elapsed time the bot does not track yet.
    d.rounded_rectangle((235, 1275, 677, 1284), radius=5, fill=(82, 67, 112, 255))
    d.rounded_rectangle((235, 1275, 421, 1284), radius=5, fill=(*PINK, 255))
    d.ellipse((412, 1268, 430, 1286), fill=(255, 245, 255, 255))
    d.text((235, 1294), "NOW STREAMING", font=_font(11, True), fill=CYAN)
    d.text((595, 1294), _time_label(current_track.get("duration")),
           font=_font(12, True), fill=(226, 213, 246, 255))

    d.text((718, 1195), "◀", font=_font(25, True), fill=CYAN)
    d.ellipse((765, 1178, 829, 1242), outline=(*PINK, 255), width=4)
    d.text((785, 1187), "Ⅱ", font=_font(28, True), fill=WHITE)
    d.text((847, 1195), "▶", font=_font(25, True), fill=CYAN)

    # Clean branded footer, kept outside the player panel.
    d.text((65, 1370), str(brand or "MIKASAVERSE").upper(),
           font=_font(15, True), fill=(231, 213, 250, 255))
    d.text((680, 1370), "LISTEN  ·  ENJOY  ·  REPEAT",
           font=_fit(d, "LISTEN  ·  ENJOY  ·  REPEAT", 225, 11, 9),
           fill=(173, 154, 207, 255))
    for i, height in enumerate([7, 12, 19, 11, 22, 13, 8]):
        x = 480 + i * 12
        wave_color = PINK if i % 2 else CYAN
        d.rounded_rectangle((x, 1378-height, x+5, 1378),
                            radius=2, fill=(*wave_color, 220))

    result = BytesIO()
    canvas.convert("RGB").save(result, format="JPEG", quality=93, optimize=True)
    result.seek(0)
    return result
