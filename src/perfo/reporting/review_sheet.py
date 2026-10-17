"""Review sheet: a PNG with the crops that need action, their reading, proposal and reasons (no UI needed)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from perfo.domain.routing import describe


def _font(size: int) -> ImageFont.ImageFont:
    for p in ("data/fonts/ArchivoNarrow.ttf", "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def review_sheet(storage: Any, values: list[Any], out_path: str | Path, title: str) -> str:
    rows = []
    for i, v in enumerate(values, 1):
        img = None
        if v.crop_key:
            img = Image.open(io.BytesIO(storage.get(v.crop_key))).convert("RGB")
            if img.height > 56:
                img = img.resize((max(1, int(img.width * 56 / img.height)), 56))
            if img.width > 420:
                img = img.resize((420, max(1, int(img.height * 420 / img.width))))
        rows.append((i, v, img))
    W, row_h = 1500, 92
    sheet = Image.new("RGB", (W, 70 + row_h * max(1, len(rows))), "white")
    d = ImageDraw.Draw(sheet)
    f_title, f, f_small = _font(26), _font(19), _font(16)
    d.text((16, 16), title, font=f_title, fill=(20, 20, 20))
    y = 64
    for i, v, img in rows:
        d.rectangle((10, y, W - 10, y + row_h - 6), outline=(220, 220, 220))
        d.text((18, y + 8), f"{i:>2}. {v.field_id}", font=f, fill=(30, 64, 175))
        if img is not None:
            sheet.paste(img, (18, y + 32))
        elif v.mark:
            d.text((18, y + 40), f"[{v.mark}]", font=f, fill=(120, 120, 120))
        conf = f"{v.confidence:.3f}" if v.confidence is not None else "—"
        d.text((470, y + 8), f"leído: {v.text!r}", font=f, fill=(20, 20, 20))
        d.text((470, y + 32), f"propuesto: {v.value!r} · confianza {conf}", font=f, fill=(20, 20, 20))
        reasons = "; ".join(describe(r) for r in v.reasons)
        d.text((470, y + 56), reasons[:120], font=f_small, fill=(185, 28, 28))
        if v.candidates:
            cand = " | ".join(f"{c['code']} {c['description']} ({c['score']:.2f})" for c in v.candidates[:3])
            d.text((1000, y + 8), "candidatas:", font=f_small, fill=(90, 90, 90))
            d.text((1000, y + 30), cand[:70], font=f_small, fill=(90, 90, 90))
            d.text((1000, y + 50), cand[70:140], font=f_small, fill=(90, 90, 90))
        y += row_h
    sheet.save(out_path)
    return str(out_path)
