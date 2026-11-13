"""Handwriting simulation: per-operator style (font, slant, weight, jitter, size) applied to text."""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from perfo.synth.real_digits import DigitHand

INKS = [(22, 38, 130), (18, 30, 95), (25, 25, 30), (35, 35, 45), (30, 55, 150)]


@dataclass(frozen=True)
class HandStyle:
    font_path: str
    slant: float  # horizontal shear factor
    weight: int  # stroke width in px
    jitter: float  # baseline jitter (px at 40 px height)
    size: float  # relative size factor
    spacing: float  # extra letter spacing (fraction of height)
    ink: tuple[int, int, int]
    rotation: float  # base rotation in degrees


def make_style(rng: random.Random, fonts: list[str]) -> HandStyle:
    return HandStyle(
        font_path=rng.choice(fonts),
        slant=rng.uniform(-0.32, 0.22),
        weight=rng.choice([0, 0, 1, 1, 2]),
        jitter=rng.uniform(0.5, 3.5),
        size=rng.uniform(0.85, 1.12),
        spacing=rng.uniform(-0.03, 0.10),
        ink=rng.choice(INKS),
        rotation=rng.uniform(-1.5, 1.5),
    )


@lru_cache(maxsize=512)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def render_text(text: str, style: HandStyle, target_h: int, rng: random.Random, digits: DigitHand | None = None) -> Image.Image:
    """Renders text as an RGBA patch roughly target_h pixels tall. With `digits`, digits are real handwritten samples."""
    size = max(10, int(target_h * 1.15 * style.size))
    scale = target_h / 40.0
    chars = list(text)
    glyphs = []
    x = 0.0
    pad = int(size * 0.6)
    for ch in chars:
        fsize = max(8, int(size * rng.uniform(0.94, 1.06)))
        font = _font(style.font_path, fsize)
        if digits is not None and ch.isdigit():
            patch, top = digits.glyph(ch, font, style.ink, style.weight, rng)
            w = patch.width + 0.04 * size
            dy = rng.gauss(0, style.jitter * scale)
            glyphs.append((patch, x, dy + top, None))
        else:
            w = font.getlength(ch)
            dy = rng.gauss(0, style.jitter * scale)
            glyphs.append((ch, x, dy, font))
        x += w + style.spacing * size + rng.gauss(0, 0.6 * scale)
    width = int(x + 2 * pad)
    height = int(size * 1.8 + 2 * pad)
    img = Image.new("RGBA", (max(width, 4), max(height, 4)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    alpha = rng.randint(205, 255)
    color = (*style.ink, alpha)
    for ch, gx, dy, font in glyphs:
        if font is None:  # real digit patch
            img.alpha_composite(ch, (int(pad + gx), max(0, int(pad + size * 0.25 + dy))))
            continue
        draw.text((pad + gx, pad + size * 0.25 + dy), ch, font=font, fill=color, stroke_width=style.weight, stroke_fill=color)
    # slant (shear) and slight rotation
    shear = style.slant + rng.gauss(0, 0.03)
    w, h = img.size
    offset = abs(shear) * h
    img = img.transform((int(w + offset), h), Image.Transform.AFFINE, (1, shear, -offset if shear > 0 else 0, 0, 1, 0), resample=Image.Resampling.BICUBIC)
    img = img.rotate(style.rotation + rng.gauss(0, 0.8), resample=Image.Resampling.BICUBIC, expand=True)
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def scribble(rng: random.Random, w: int, h: int, ink: tuple[int, int, int]) -> Image.Image:
    """A signature-like scribble."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pts = []
    x = rng.uniform(0.05, 0.2) * w
    for _ in range(rng.randint(8, 16)):
        x += rng.uniform(0.03, 0.09) * w
        y = rng.uniform(0.2, 0.8) * h
        pts.append((min(x, w - 2), y))
    draw.line(pts, fill=(*ink, 240), width=rng.choice([2, 3]), joint="curve")
    return img
