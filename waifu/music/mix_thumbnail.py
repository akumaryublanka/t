"""Poster renderer for the MIKASAVERSE related-song recommendation screen."""
from __future__ import annotations

from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter

from waifu.music.thumbnail import (
    _cover,
    _font,
    _load_artwork,
    _load_mikasa_portrait,
    _time_label,
)

W, H = 960, 1440
PINK = (244, 92, 207)
PURPLE = (153, 83, 232)
CYAN = (94, 204, 255)
INK = (20, 7, 48)


def _rounded_cover(canvas: Image.Image, source, xy, size, border=PINK):
    x, y = xy
    cover = _load_artwork(source)
    if cover is None:
        cover = Image.new("RGB", size, (45, 25, 75))
        cd = ImageDraw.Draw(cover)
        cd.text((size[0] // 3, size[1] // 3), "♫", font=_font(42, True), fill=(255, 180, 240))
    cover = _cover(cover, size).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0]-1, size[1]-1), radius=12, fill=255)
    canvas.paste(cover, (x, y), mask)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((x-5, y-5, x+size[0]+5, y+size[1]+5),
                        radius=16, outline=border, width=4)


def _fit(draw, text, max_width, size=19, minimum=12):
    text = str(text or "Unknown")
    while size > minimum:
        font = _font(size, True)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 1
    return _font(minimum, True)


def create_mix_thumbnail(
    tracks: list[dict],
    current_track: Optional[dict] = None,
    *,
    brand: str = "MIKASAVERSE",
) -> BytesIO:
    """Create a tall anime/pixel-neon recommendation poster with five song cards."""
    tracks = list(tracks or [])[:5]
    current_track = current_track or {}
    canvas = Image.new("RGBA", (W, H), (*INK, 255))

    # Neon night-sky background and soft pixel-arcade glow.
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-140, -180, 530, 510), fill=(*PURPLE, 115))
    gd.ellipse((440, -130, 1080, 480), fill=(*PINK, 75))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(100)))
    d = ImageDraw.Draw(canvas, "RGBA")
    for x in range(24, W, 32):
        d.line((x, 0, x, H), fill=(159, 104, 225, 20), width=1)
    for y in range(0, H, 32):
        d.line((0, y, W, y), fill=(159, 104, 225, 20), width=1)

    # Outer frame.
    d.rounded_rectangle((12, 12, W-12, H-12), radius=34,
                        fill=(16, 5, 42, 245), outline=(247, 225, 255, 255), width=7)
    d.rounded_rectangle((27, 27, W-27, H-27), radius=26,
                        outline=(*PURPLE, 255), width=4)

    # Top anime heroine panel. Uses the project's bundled Mikasa-style art.
    portrait = _load_mikasa_portrait((370, 360), PINK).convert("RGBA")
    portrait = portrait.resize((370, 360), Image.Resampling.LANCZOS)
    canvas.alpha_composite(portrait, (35, 42))
    d = ImageDraw.Draw(canvas, "RGBA")
    # Blossom/pixel sparkles.
    for x, y in [(48, 62), (100, 95), (360, 52), (410, 170), (300, 340), (70, 350), (870, 65), (900, 310)]:
        d.ellipse((x-8, y-4, x+8, y+4), fill=(*PINK, 220))
    d.rounded_rectangle((390, 76, 900, 325), radius=26,
                        fill=(28, 8, 66, 235), outline=(*PINK, 255), width=5)
    d.text((470, 95), "♛", font=_font(42, True), fill=(255, 205, 89, 255))
    d.text((430, 150), "Relative", font=_font(49, True), fill=(255, 245, 255, 255))
    d.text((415, 211), "Suggestion", font=_fit(d, "Suggestion", 455, 43, 28), fill=(*PINK, 255))
    d.text((550, 276), "♫  MIKASA MIX  ♫", font=_font(17, True), fill=(*CYAN, 255))

    # Five recommendation tiles with numbered badges, cover art, title, artist and duration.
    top = 390
    for index, track in enumerate(tracks):
        y = top + index * 137
        d.rounded_rectangle((43, y, 917, y+119), radius=24,
                            fill=(31, 10, 68, 245), outline=(*PURPLE, 255), width=4)
        d.rounded_rectangle((51, y+7, 909, y+112), radius=18,
                            outline=(*PINK, 180), width=2)
        d.rounded_rectangle((63, y+17, 143, y+101), radius=17,
                            fill=(45, 17, 91, 255), outline=(*PINK, 255), width=4)
        number_font = _font(39, True)
        d.text((88 if index < 9 else 78, y+32), str(index+1), font=number_font,
               fill=(255, 240, 255, 255))
        _rounded_cover(canvas, track.get("thumbnail"), (165, y+17), (86, 86),
                       border=CYAN if index % 2 else PINK)
        d = ImageDraw.Draw(canvas, "RGBA")
        title = str(track.get("title") or "Unknown track")
        artist = str(track.get("artist") or track.get("uploader") or "Unknown artist")
        d.text((275, y+24), title, font=_fit(d, title, 500, 19, 13),
               fill=(255, 249, 255, 255))
        d.text((275, y+64), "♫ " + artist, font=_fit(d, artist, 480, 15, 11),
               fill=(185, 164, 214, 255))
        d.text((795, y+70), _time_label(track.get("duration")),
               font=_font(14, True), fill=(218, 204, 240, 255))
        # Tiny play tile at right.
        d.rounded_rectangle((850, y+30, 900, y+84), radius=13,
                            fill=(*PURPLE, 255), outline=(*PINK, 255), width=3)
        d.text((861, y+37), "▶", font=_font(25, True), fill=(255, 255, 255, 255))

    # Current-playing strip, mirroring the lower player module in the reference.
    panel_y = 1095
    d.rounded_rectangle((42, panel_y, 918, 1335), radius=28,
                        fill=(19, 7, 48, 250), outline=(*PINK, 255), width=5)
    d.rounded_rectangle((67, panel_y-22, 305, panel_y+22), radius=18,
                        fill=(*PINK, 255), outline=(255, 222, 250, 255), width=3)
    d.text((89, panel_y-14), "NOW PLAYING  ♫", font=_font(19, True),
           fill=(36, 7, 48, 255))
    _rounded_cover(canvas, current_track.get("thumbnail"), (76, 1148), (130, 130), border=CYAN)
    d = ImageDraw.Draw(canvas, "RGBA")
    now_title = str(current_track.get("title") or "Music is playing")
    now_artist = str(current_track.get("artist") or current_track.get("uploader") or "MIKASAVERSE")
    d.text((235, 1160), now_title, font=_fit(d, now_title, 440, 19, 12),
           fill=(255, 249, 255, 255))
    d.text((235, 1200), now_artist, font=_fit(d, now_artist, 420, 15, 11),
           fill=(184, 164, 212, 255))
    d.text((235, 1247), "━━━●━━━━━━━━", font=_font(19, True), fill=(*PINK, 255))
    d.text((235, 1280), "LIVE IN YOUR GROUP  ·  " + _time_label(current_track.get("duration")),
           font=_font(12, True), fill=(198, 184, 220, 255))
    d.text((710, 1185), "◀", font=_font(28, True), fill=(*CYAN, 255))
    d.ellipse((770, 1170, 836, 1236), outline=(*PINK, 255), width=4)
    d.text((790, 1180), "Ⅱ", font=_font(29, True), fill=(255, 255, 255, 255))
    d.text((860, 1185), "▶", font=_font(28, True), fill=(*CYAN, 255))
    d.text((75, 1350), str(brand).upper(), font=_font(15, True), fill=(225, 207, 240, 255))
    d.text((690, 1350), "LISTEN  ·  ENJOY  ·  REPEAT", font=_font(12, True), fill=(177, 154, 205, 255))

    result = BytesIO()
    canvas.convert("RGB").save(result, format="JPEG", quality=91, optimize=True)
    result.seek(0)
    return result
