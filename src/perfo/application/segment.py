"""Deterministic template segmentation: alignment check, crops, empty-cell and repetition-mark detection."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from perfo.domain.fields import FieldSpec, Template


@dataclass
class CropImage:
    spec: FieldSpec
    box_px: tuple[int, int, int, int]
    png: bytes | None  # recognizer input (ink-cropped, red removed); None when empty or mark
    is_empty: bool
    ink_ratio: float
    mark: str | None  # "repeat"


@dataclass
class SegmentationResult:
    ok: bool
    cause: str | None
    crops: list[CropImage]
    alignment: dict[str, Any]
    signatures: dict[str, bool]


def _anchors(template: Template) -> list[tuple[int, int]]:
    raw = template.raw
    W, H = template.width, template.height
    if raw.get("anchors"):  # explicit anchors (outer frame corners) declared by the template
        return [(round(x * W), round(y * H)) for x, y in raw["anchors"]]
    pts: list[tuple[int, int]] = []
    for t in raw.get("tables", []):
        x0, y0, x1, y1 = t["box"]
        pts += [(round(x0 * W), round(y0 * H)), (round(x1 * W), round(y0 * H)), (round(x0 * W), round(y1 * H)), (round(x1 * W), round(y1 * H))]
    fr = [f["frame"] for f in raw.get("fields", []) if "frame" in f]
    if fr:
        pts += [(round(fr[0][0] * W), round(fr[0][1] * H)), (round(fr[5][2] * W), round(fr[5][1] * H))]
    return pts


@lru_cache(maxsize=4)
def _reference_gray(path: str) -> np.ndarray:
    return cv2.imread(path, cv2.IMREAD_GRAYSCALE)


def check_alignment(aligned_gray: np.ndarray, template: Template, reference_path: str, search_px: int | None = None) -> dict[str, Any]:
    """Measures residual misalignment at template anchors (table and header corners)."""
    ref = _reference_gray(reference_path)
    tol = template.alignment.tolerance_px
    half, search = 60, int(search_px or tol * 2 + 10)
    offsets = []
    for x, y in _anchors(template):
        tpl = ref[max(0, y - half) : y + half, max(0, x - half) : x + half]
        y0, x0 = max(0, y - half - search), max(0, x - half - search)
        win = aligned_gray[y0 : y + half + search, x0 : x + half + search]
        if tpl.size == 0 or win.shape[0] < tpl.shape[0] or win.shape[1] < tpl.shape[1]:
            continue
        res = cv2.matchTemplate(255 - win, 255 - tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        exp_x, exp_y = max(0, x - half) - x0, max(0, y - half) - y0
        dx, dy = loc[0] - exp_x, loc[1] - exp_y
        offsets.append({"anchor": [x, y], "dx": int(dx), "dy": int(dy), "score": round(float(score), 3)})
    errors = sorted(max(abs(o["dx"]), abs(o["dy"])) if o["score"] > 0.3 else 999 for o in offsets)
    allowed = int(template.alignment.max_outlier_ratio * len(errors))  # 0 by default: every anchor must agree
    worst = errors[-(allowed + 1)] if len(errors) > allowed else 999
    outliers = sum(1 for e in errors if e > tol)
    return {"anchors": offsets, "max_offset_px": worst, "tolerance_px": tol, "ok": worst <= tol, "outliers": outliers}


def _accept_reliable_anchors(align: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Second look at a page that failed the alignment check (opt-in: segmentation.alignment_min_score).

    In phone photos a shadow or a dark corner can make one anchor match the wrong place with a low score, and that
    single anchor fails a page whose other anchors agree within a pixel or two. If enough anchors are reliable
    (score above alignment_min_score, at least alignment_min_reliable of them) and all of those are within tolerance,
    the page is accepted and the ignored anchors are recorded. Pages that pass the check are never touched.
    """
    min_score = cfg.get("alignment_min_score")
    if min_score is None or not align["anchors"]:
        return align
    reliable = [o for o in align["anchors"] if o["score"] > float(min_score)]
    if len(reliable) < float(cfg.get("alignment_min_reliable", 0.7)) * len(align["anchors"]):
        return align
    worst = max(max(abs(o["dx"]), abs(o["dy"])) for o in reliable)
    if worst > align["tolerance_px"]:
        return align
    ignored = [o["anchor"] for o in align["anchors"] if o["score"] <= float(min_score)]
    return align | {"ok": True, "max_offset_px": worst, "ignored_anchors": ignored, "max_offset_all_px": align["max_offset_px"]}


def _drop_red(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = ((h < 10) | (h > 168)) & (s > 70) & (v > 60)
    out = bgr.copy()
    out[red] = (250, 250, 250)
    return out


def _flat_paper(img: np.ndarray) -> np.ndarray:
    """Divides by the paper itself: a photo's shading must not read as ink."""
    h, w = img.shape[:2]
    paper = cv2.morphologyEx(cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    paper = cv2.resize(cv2.GaussianBlur(paper, (0, 0), 8), (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(img.astype(np.float32) / np.maximum(paper, 1) * 255, 0, 255).astype(np.uint8)


def _clean_photo(aligned_bgr: np.ndarray, threshold: int) -> np.ndarray:
    """Flat paper and no ruled lines: at 110 ppp the lines are as thick as a stroke and cross every cell."""
    dark = _drop_red(_flat_paper(aligned_bgr)).min(axis=2)  # the darkest channel: blue pen barely shows in gray
    faint = 228  # a pale pen on flat paper: the fixed threshold would cut its strokes
    ink = (dark < faint).astype(np.uint8)
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, 45), np.uint8))
    for lean in (-3, -1, 0, 1, 3):  # the column lines of a photo lean a little
        kernel = cv2.line(np.zeros((40, 7), np.uint8), (3 + lean, 0), (3 - lean, 39), 1, 1)
        lines |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    ink[cv2.dilate(lines, np.ones((5, 5), np.uint8)) > 0] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= 40  # what is left of a line is smaller than any stroke
    keep[0] = False
    return cv2.cvtColor(np.where(keep[labels], dark * (threshold / faint), 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def _ink_mask(gray: np.ndarray, threshold: int, min_px: int) -> np.ndarray:
    ink = (gray < threshold).astype(np.uint8)
    h, w = ink.shape
    band = 6
    # remove residual printed grid lines touching the borders
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(8, int(w * 0.5)), 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, int(h * 0.8)))))
    edge = np.zeros_like(ink)
    edge[:band, :] = edge[-band:, :] = 1
    edge_v = np.zeros_like(ink)
    edge_v[:, :band] = edge_v[:, -band:] = 1
    ink[(horiz & edge).astype(bool)] = 0
    ink[(vert & edge_v).astype(bool)] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_px
    return keep[labels].astype(np.uint8)


def segment(aligned_bgr: np.ndarray, template: Template, reference_path: str, cfg: dict[str, Any], fields: list[FieldSpec] | None = None) -> SegmentationResult:
    gray_full = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY)
    align = check_alignment(gray_full, template, reference_path)
    if not align["ok"]:
        align = _accept_reliable_anchors(align, cfg)
    if not align["ok"]:
        missing = sum(1 for o in align["anchors"] if o["score"] <= 0.3)
        detail = f"{missing} anclas de la plantilla no encontradas" if missing else f"desfase {align['max_offset_px']} px > {align['tolerance_px']} px"
        return SegmentationResult(False, f"Alineación de plantilla fuera de tolerancia ({detail})", [], align, {})

    inset = int(cfg.get("inset_px", 4))
    thr = int(cfg.get("binarize_threshold", 165))
    if template.raw.get("photo_cleanup"):  # templates measured on real photos
        aligned_bgr = _clean_photo(aligned_bgr, thr)
    empty_ratio = float(cfg.get("empty_ink_ratio", 0.006))
    min_ink_px = int(cfg.get("min_ink_px", 60))
    min_px = int(cfg.get("min_component_px", 12))
    rep_w = float(cfg.get("repeat_mark_max_width_ratio", 0.18))
    rep_h = float(cfg.get("repeat_mark_min_height_ratio", 0.85))
    # where digits are as tall as the row, a ditto mark is told apart by a thin stroke running through the cell
    rep_aspect = float(cfg.get("repeat_mark_min_aspect", 0.0))
    rep_edges = bool(cfg.get("repeat_mark_touch_edges", False))

    crops: list[CropImage] = []
    for spec in fields if fields is not None else template.prioritized():
        x0, y0, x1, y1 = template.box_px(spec)
        box = (x0, y0, x1, y1)
        cell = aligned_bgr[y0 + inset : y1 - inset, x0 + inset : x1 - inset]
        if cell.size == 0:
            crops.append(CropImage(spec, box, None, True, 0.0, None))
            continue
        clean = _drop_red(cell)
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        ink = _ink_mask(gray, thr, min_px)
        ratio = float(ink.mean())
        if int(ink.sum()) < min_ink_px:
            crops.append(CropImage(spec, box, None, True, round(ratio, 5), None))
            continue
        ys, xs = np.nonzero(ink)
        bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        ch, cw = ink.shape
        through = not rep_edges or (ys.min() <= 1 and ys.max() >= ch - 2)
        if spec.is_cell and bw <= rep_w * cw and bh >= rep_h * ch and bh >= rep_aspect * bw and through:
            crops.append(CropImage(spec, box, None, False, round(ratio, 5), "repeat"))
            continue
        m = 8
        cx0, cx1 = max(0, xs.min() - m), min(cw, xs.max() + m + 1)
        cy0, cy1 = max(0, ys.min() - m), min(ch, ys.max() + m + 1)
        out = gray[cy0:cy1, cx0:cx1]
        # whiten everything that is not ink-adjacent background noise
        out = np.where(cv2.dilate(ink, np.ones((3, 3), np.uint8))[cy0:cy1, cx0:cx1] > 0, out, 255).astype(np.uint8)
        ok, buf = cv2.imencode(".png", cv2.cvtColor(out, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 3])
        crops.append(CropImage(spec, box, buf.tobytes() if ok else None, False, round(ratio, 5), None))

    signatures = {}
    for spec in template.signatures():
        x0, y0, x1, y1 = template.box_px(spec)
        g = cv2.cvtColor(_drop_red(aligned_bgr[y0:y1 - 34, x0:x1]), cv2.COLOR_BGR2GRAY)
        signatures[spec.id] = float(_ink_mask(g, thr, min_px).mean()) > empty_ratio
    return SegmentationResult(True, None, crops, align, signatures)
