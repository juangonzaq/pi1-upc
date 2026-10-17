"""Renders the blank form from template v1 and fills it with handwriting."""

from __future__ import annotations

import math
import random
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from perfo.synth.content import FormContent
from perfo.synth.handwriting import HandStyle, render_text, scribble

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


def fill_form(blank: Image.Image, template: dict[str, Any], content: FormContent, style: HandStyle, rng: random.Random) -> Image.Image:
    page = blank.copy()
    boxes = _field_boxes(template)
    draw = ImageDraw.Draw(page)

    def write(fid: str, text: str) -> None:
        box = boxes[fid]
        bh = box[3] - box[1]
        target = int(bh * rng.uniform(0.55, 0.72))
        patch = render_text(text, style, target, rng)
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
        patch = render_text(dg["text"], style, int((first[3] - first[1]) * 0.75), rng)
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
