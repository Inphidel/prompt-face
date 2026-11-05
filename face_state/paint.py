"""Draw the face into a fixed panel.

The panel size does not follow the string. The face is anchored at the
middle of the image. Arms grow outward from that face and clip at the panel
edge. A custom emote is the same call with empty arms, so the whole line
stays centered at the same font size. OBS then scales and rotates the panel.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "fonts" / "FaceText-Regular.ttf"
BASE_FONT = 80
# Wide enough for the longest saved face plus a short arm string on each side.
PANEL_WIDTH = 1400
PANEL_HEIGHT = 180
GAP = 10

_fonts: dict[int, ImageFont.FreeTypeFont] = {}


def canvas_size(font_px: int) -> tuple[int, int]:
    scale = max(int(font_px), 1) / BASE_FONT
    return (round(PANEL_WIDTH * scale), round(PANEL_HEIGHT * scale))


def _font(font_px: int) -> ImageFont.FreeTypeFont:
    size = max(int(font_px), 1)
    cached = _fonts.get(size)
    if cached is None:
        cached = ImageFont.truetype(str(FONT_PATH), size)
        _fonts[size] = cached
    return cached


def _width(draw: ImageDraw.ImageDraw, text: str, font, stroke: int) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke, anchor="lt")
    return box[2] - box[0]


def paint_panel(
    face: str,
    left: str,
    right: str,
    dest: Path,
    font_px: int = BASE_FONT,
    outline: int = 4,
    fill: tuple[int, int, int, int] = (255, 255, 255, 255),
    stroke: tuple[int, int, int, int] = (0, 0, 0, 255),
) -> Path:
    """Write a transparent PNG. The face is centered. Arms sit against it."""
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"face font is missing: {FONT_PATH}")
    width, height = canvas_size(font_px)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(font_px)
    stroke_px = max(int(outline), 0)
    gap = round(GAP * max(int(font_px), 1) / BASE_FONT)
    center = (width / 2, height / 2)
    face = face or ""
    left = left or ""
    right = right or ""
    face_width = _width(draw, face, font, stroke_px)
    if face:
        draw.text(center, face, font=font, fill=fill, stroke_width=stroke_px, stroke_fill=stroke, anchor="mm")
    if left:
        draw.text(
            (center[0] - face_width / 2 - gap, center[1]),
            left,
            font=font,
            fill=fill,
            stroke_width=stroke_px,
            stroke_fill=stroke,
            anchor="rm",
        )
    if right:
        draw.text(
            (center[0] + face_width / 2 + gap, center[1]),
            right,
            font=font,
            fill=fill,
            stroke_width=stroke_px,
            stroke_fill=stroke,
            anchor="lm",
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest
