"""Renders the blank form from template v1 and fills it with handwriting."""

from __future__ import annotations

import math
import random
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from perfo.synth.content import FormContent
from perfo.synth.handwriting import HandStyle, render_text, scribble
from perfo.synth.real_digits import DigitHand

PAPER = (251, 250, 246)
LINE = (45, 45, 50)
RED = (200, 30, 35)


def _px(box: list[float], W: int, H: int) -> tuple[int, int, int, int]:
    return round(box[0] * W), round(box[1] * H), round(box[2] * W), round(box[3] * H)


@lru_cache(maxsize=64)
def _pf(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _wrap_center(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], font_path: str, size: int) -> None:
    x0, y0, x1, y1 = box
    font = _pf(font_path, size)
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if font.getlength(test) <= (x1 - x0 - 8) or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lh = size + 4
    ty = y0 + ((y1 - y0) - lh * len(lines)) / 2
    for line in lines:
        tw = font.getlength(line)
        draw.text((x0 + (x1 - x0 - tw) / 2, ty), line, font=font, fill=LINE)
        ty += lh


def render_blank(template: dict[str, Any], printed_font: str) -> Image.Image:
    W, H = template["reference"]["width"], template["reference"]["height"]
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    st = template["static"]
    d.text((70, 52), st["title"], font=_pf(printed_font, 56), fill=LINE)
    d.text((70, 118), st["subtitle"], font=_pf(printed_font, 30), fill=LINE)
    d.text((W - 900, 60), "Plantilla " + template["version"] + " · Hoja 1/1", font=_pf(printed_font, 28), fill=LINE)

    for f in template["fields"]:
        x0, y0, x1, y1 = _px(f["frame"], W, H)
        d.rectangle((x0, y0, x1, y1), outline=LINE, width=3)
        d.text((x0 + 10, y0 + 6), f["label"], font=_pf(printed_font, 24), fill=LINE)

    for t in template["tables"]:
        x0, y0, x1, y1 = _px(t["box"], W, H)
        hh = round(t["header_height"] * H)
        rh = round(t["row_height"] * H)
        d.text((x0, y0 - 38), t["title"], font=_pf(printed_font, 30), fill=LINE)
        d.rectangle((x0, y0, x1, y1), outline=LINE, width=3)
        d.line((x0, y0 + hh, x1, y0 + hh), fill=LINE, width=3)
        for r in range(1, t["rows"]):
            y = round((t["box"][1] + t["header_height"] + r * t["row_height"]) * H)
            d.line((x0, y, x1, y), fill=LINE, width=2)
        for c in t["columns"]:
            cx0, cx1 = round(c["x0"] * W), round(c["x1"] * W)
            if cx0 > x0:
                d.line((cx0, y0, cx0, y1), fill=LINE, width=2)
            _wrap_center(d, c["label"], (cx0, y0, cx1, y0 + hh), printed_font, 20 if hh > 60 else 18)
        del rh

    lx0, ly0, lx1, ly1 = _px(st["legend_box"], W, H)
    d.rectangle((lx0, ly0, lx1, ly1), outline=LINE, width=3)
    d.text((lx0 + 12, ly0 + 8), "LISTA DE CÓDIGOS", font=_pf(printed_font, 26), fill=LINE)
    y, col_x = ly0 + 46, lx0 + 12
    small = _pf(printed_font, 19)
    for group, codes in st["codes"].items():
        d.text((col_x, y), group, font=_pf(printed_font, 21), fill=LINE)
        y += 26
        for code, desc in codes.items():
            if y > ly1 - 26:
                y, col_x = ly0 + 46, col_x + (lx1 - lx0) // 2
            d.text((col_x + 8, y), f"{code}  {desc}", font=small, fill=LINE)
            y += 23
        y += 6
    rx0, ry0, rx1, ry1 = _px(st["rule_box"], W, H)
    d.rectangle((rx0, ry0, rx1, ry1), outline=LINE, width=3)
    _wrap_center(d, st["rule_note"], (rx0 + 6, ry0, rx1 - 6, ry1), printed_font, 22)

    for s in template["signatures"]:
        x0, y0, x1, y1 = _px(s["box"], W, H)
        d.line((x0, y1 - 30, x1, y1 - 30), fill=LINE, width=2)
        label = s["label"]
        tw = _pf(printed_font, 20).getlength(label)
        d.text((x0 + (x1 - x0 - tw) / 2, y1 - 26), label, font=_pf(printed_font, 20), fill=LINE)
    return img


def _field_boxes(template: dict[str, Any]) -> dict[str, tuple[int, int, int, int]]:
    W, H = template["reference"]["width"], template["reference"]["height"]
    out = {f["id"]: _px(f["box"], W, H) for f in template["fields"]}
    for t in template["tables"]:
        for r in range(1, t["rows"] + 1):
            y0 = t["box"][1] + t["header_height"] + (r - 1) * t["row_height"]
            y1 = y0 + t["row_height"]
            for c in t["columns"]:
                out[f"{t['id']}.r{r:02d}.{c['id']}"] = _px([c["x0"], y0, c["x1"], y1], W, H)
    for s in template["signatures"]:
        out[s["id"]] = _px(s["box"], W, H)
    return out


def _paste_in_box(page: Image.Image, patch: Image.Image, box: tuple[int, int, int, int], rng: random.Random, align_left: bool = True) -> None:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    pw, ph = patch.size
    max_w = int(bw * 1.04)
    if pw > max_w:
        ratio = max_w / pw
        patch = patch.resize((max(1, int(pw * ratio)), max(1, int(ph * ratio))), Image.Resampling.LANCZOS)
        pw, ph = patch.size
    if align_left:
        px = x0 + rng.randint(4, max(5, min(30, bw - pw))) if bw > pw else x0 - (pw - bw) // 3
    else:
        px = x0 + (bw - pw) // 2
    py = y0 + (bh - ph) // 2 + rng.randint(-4, 4)
    page.paste(patch, (px, py), patch)


def _wrong_value(text: str, rng: random.Random) -> str:
    """The value the operator wrote first and then crossed out: one digit changed."""
    pos = [i for i, ch in enumerate(text) if ch.isdigit()]
    i = rng.choice(pos)
    return text[:i] + str((int(text[i]) + rng.randint(1, 9)) % 10) + text[i + 1 :]


def _struck(patch: Image.Image, ink: tuple[int, int, int], rng: random.Random) -> Image.Image:
    """Crosses out a written value: one or two strokes, or a zigzag."""
    out = patch.copy()
    d = ImageDraw.Draw(out)
    w, h = out.size
    width = max(2, h // 12)
    kind = rng.choice(["line", "line", "double", "zigzag"])
    if kind == "zigzag":
        n = rng.randint(4, 7)
        pts = [(w * i / n, h * (0.25 if i % 2 else 0.75) + rng.uniform(-2, 2)) for i in range(n + 1)]
        d.line(pts, fill=(*ink, 235), width=width, joint="curve")
    else:
        for k in range(2 if kind == "double" else 1):
            y = h * (0.5 + (k - 0.5) * 0.25 if kind == "double" else 0.5) + rng.uniform(-h * 0.1, h * 0.1)
            d.line((-2, y + rng.uniform(-3, 3), w + 2, y + rng.uniform(-3, 3)), fill=(*ink, 235), width=width)
    return out


def _side_by_side(a: Image.Image, b: Image.Image, gap: int) -> Image.Image:
    out = Image.new("RGBA", (a.width + gap + b.width, max(a.height, b.height)), (0, 0, 0, 0))
    out.alpha_composite(a, (0, (out.height - a.height) // 2))
    out.alpha_composite(b, (a.width + gap, (out.height - b.height) // 2))
    return out


def fill_form(blank: Image.Image, template: dict[str, Any], content: FormContent, style: HandStyle, rng: random.Random,
              realism: dict[str, Any] | None = None, digits: DigitHand | None = None) -> Image.Image:
    """Fills the blank form. `realism` (synth.realism) adds what real forms show and fonts alone do not: digits and
    values that spill over the cell lines, and values crossed out and rewritten. Without it the output is unchanged."""
    page = blank.copy()
    boxes = _field_boxes(template)
    draw = ImageDraw.Draw(page)
    rr = random.Random(rng.random()) if realism is not None else None  # realism decisions, separate from the base draws
    p_over = float((realism or {}).get("overflow", 0.0))
    p_strike = float((realism or {}).get("strikeout", 0.0))

    def write(fid: str, text: str) -> None:
        box = boxes[fid]
        bh = box[3] - box[1]
        if rr is None:
            target = int(bh * rng.uniform(0.55, 0.72))
            patch = render_text(text, style, target, rng)
            _paste_in_box(page, patch, box, rng)
            return
        over = rr.random() < p_over
        target = int(bh * (rr.uniform(0.68, 0.88) if over else rng.uniform(0.55, 0.72)))
        patch = render_text(text, style, target, rng, digits)
        if any(ch.isdigit() for ch in text) and rr.random() < p_strike:
            wrong = _struck(render_text(_wrong_value(text, rr), style, target, rng, digits), style.ink, rr)
            patch = _side_by_side(wrong, patch, max(4, target // 4))
            content.flags.setdefault("strikeout", []).append(fid)
        if over:
            # spills past the cell: the stroke crosses the ruled lines above or below, as on paper filled in a hurry
            x0, y0, x1, y1 = box
            shift = int(bh * rr.uniform(0.2, 0.4)) * rr.choice([-1, 1])
            box = (x0, y0 + shift, x1 + int((x1 - x0) * 0.08), y1 + shift)
            content.flags.setdefault("overflow", []).append(fid)
        _paste_in_box(page, patch, box, rng)

    for fid, spec in content.fields.items():
        if spec["text"]:
            write(fid, spec["text"])
        elif spec["mark"] == "repeat":
            x0, y0, x1, y1 = boxes[fid]
            cx = (x0 + x1) // 2 + rng.randint(-12, 12)
            draw.line((cx, y0 - 2, cx + rng.randint(-3, 3), y1 + 2), fill=style.ink, width=rng.choice([3, 4]))
    for fid, text in content.extra_text.items():
        if fid in boxes:
            write(fid, text)

    # labor written diagonally across several rows
    for dg in content.diagonal:
        first = boxes[f"{dg['table']}.r{dg['row']:02d}.{dg['col']}"]
        last = boxes[f"{dg['table']}.r{dg['row'] + dg['span'] - 1:02d}.{dg['col']}"]
        x0, y0, x1, y1 = first[0], first[1], last[2], last[3]
        patch = render_text(dg["text"], style, int((first[3] - first[1]) * 0.75), rng, digits if realism is not None else None)
        angle = math.degrees(math.atan2((y1 - y0) * 0.55, (x1 - x0)))
        patch = patch.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        _paste_in_box(page, patch, (x0, y0, x1, y1), rng, align_left=False)

    for fid, present in content.signatures.items():
        if present:
            x0, y0, x1, y1 = boxes[fid]
            sc = scribble(rng, x1 - x0, y1 - y0 - 34, style.ink)
            page.paste(sc, (x0, y0), sc)

    # red revision marks
    if rng.random() < 0.35:
        ids = [f for f, s in content.fields.items() if s["text"]]
        for fid in rng.sample(ids, k=min(len(ids), rng.randint(1, 4))):
            x0, y0, x1, y1 = boxes[fid]
            kind = rng.choice(["check", "underline", "circle"])
            if kind == "check":
                cx, cy = x1 - rng.randint(15, 40), (y0 + y1) // 2
                draw.line([(cx - 12, cy), (cx - 3, cy + 12), (cx + 16, cy - 16)], fill=RED, width=4)
            elif kind == "underline":
                draw.line((x0 + 6, y1 - 6, x1 - 10, y1 - 8), fill=RED, width=3)
            else:
                draw.ellipse((x0 + 2, y0 + 2, x1 - 2, y1 - 2), outline=RED, width=3)
            content.flags.setdefault("red_marks", []).append(fid)
    return page
