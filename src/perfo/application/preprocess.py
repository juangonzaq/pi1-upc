"""Quality control and preprocessing: resolution, sharpness, orientation and perspective correction."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from perfo.domain.fields import Template


@dataclass
class PreprocessResult:
    ok: bool
    cause: str | None
    image: np.ndarray | None  # aligned BGR image in the reference frame
    transform: dict[str, Any] = field(default_factory=dict)


def decode_image(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


@lru_cache(maxsize=4)
def _reference_features(ref_path: str, scale: float, n_features: int) -> tuple[Any, np.ndarray, tuple[int, int]]:
    ref = cv2.imread(ref_path, cv2.IMREAD_GRAYSCALE)
    if ref is None:
        raise FileNotFoundError(f"Imagen de referencia de la plantilla no encontrada: {ref_path}")
    small = cv2.resize(ref, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    orb = cv2.ORB_create(nfeatures=n_features)
    kp, des = orb.detectAndCompute(small, None)
    pts = np.float32([k.pt for k in kp])
    return des, pts, ref.shape[:2]


def _match(des_ref: np.ndarray, pts_ref: np.ndarray, img_small: np.ndarray, n_features: int) -> tuple[np.ndarray | None, int, float]:
    orb = cv2.ORB_create(nfeatures=n_features)
    kp, des = orb.detectAndCompute(img_small, None)
    if des is None or len(kp) < 10:
        return None, 0, float("inf")
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(des, des_ref, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.78 * p[1].distance]
    if len(good) < 10:
        return None, len(good), float("inf")
    src = np.float32([kp[m.queryIdx].pt for m in good])
    dst = np.float32([pts_ref[m.trainIdx] for m in good])
    cv2.setRNGSeed(12345)  # RANSAC sampling is deterministic
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0, maxIters=5000, confidence=0.999)
    if H is None:
        return None, 0, float("inf")
    inl = mask.ravel().astype(bool)
    proj = cv2.perspectiveTransform(src[inl].reshape(-1, 1, 2), H).reshape(-1, 2)
    err = float(np.mean(np.linalg.norm(proj - dst[inl], axis=1))) if inl.any() else float("inf")
    return H, int(inl.sum()), err


def sharpness(gray: np.ndarray) -> float:
    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(small, cv2.CV_64F).var())


def preprocess(image_bytes: bytes, template: Template, reference_path: str, cfg: dict[str, Any]) -> PreprocessResult:
    img = decode_image(image_bytes)
    if img is None:
        return PreprocessResult(False, "Archivo de imagen ilegible o formato no soportado", None)
    h, w = img.shape[:2]
    long_side, ref_long = max(h, w), max(template.width, template.height)
    dpi_eq = long_side / ref_long * template.dpi
    tr: dict[str, Any] = {"input_size": [w, h], "dpi_equivalent": round(dpi_eq, 1)}
    # two levels: below min_dpi (configuration) the image is rejected; below the template resolution (TB1: 200 ppp)
    # it is processed with a quality warning and its fields go to review
    recommended = template.alignment.min_dpi
    floor = float(cfg.get("min_dpi", recommended))
    if dpi_eq < floor:
        need = math.ceil(floor / template.dpi * ref_long)
        need_rec = math.ceil(recommended / template.dpi * ref_long)
        return PreprocessResult(False, f"Resolución insuficiente: la imagen mide {w}×{h} px ({dpi_eq:.0f} ppp equivalentes; mínimo aceptado "
                                       f"{floor:.0f}, recomendado {recommended:.0f}). Use una imagen de al menos {need} px de lado mayor "
                                       f"({need_rec} px para la resolución recomendada)", None, tr)
    if dpi_eq < recommended:
        tr["low_resolution"] = True
        tr["quality_warnings"] = [f"Baja resolución: {w}×{h} px, {dpi_eq:.0f} ppp equivalentes (se recomiendan {recommended:.0f}); "
                                  "la lectura es menos fiable y todos los campos van a revisión"]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp = sharpness(gray)
    tr["sharpness"] = round(sharp, 2)
    if sharp < template.alignment.min_sharpness:
        return PreprocessResult(False, f"Imagen borrosa o ilegible (nitidez {sharp:.1f} < {template.alignment.min_sharpness})", None, tr)

    scale = float(cfg.get("work_scale", 0.35))
    n_features = int(cfg.get("orb_features", 6000))
    des_ref, pts_ref, _ = _reference_features(reference_path, scale, n_features)

    # orientation candidates: landscape page expected
    rotations = [0, 180] if w >= h else [90, 270]
    best: tuple[int, np.ndarray | None, int, float, np.ndarray] | None = None
    for rot in rotations:
        oriented = gray if rot == 0 else cv2.rotate(gray, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rot])
        f = (ref_long * scale) / max(oriented.shape)
        small = cv2.resize(oriented, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        H, inliers, err = _match(des_ref, pts_ref, small, n_features)
        if best is None or inliers > best[2]:
            best = (rot, H, inliers, err, np.diag([f, f, 1.0]))
        if inliers >= 4 * template.alignment.min_inliers:
            break
    assert best is not None
    rot, H, inliers, err_small, S_t = best
    err_full = err_small / scale
    tr.update({"rotation": rot, "inliers": inliers, "reprojection_error_px": round(err_full, 3)})
    if H is None or inliers < template.alignment.min_inliers or err_full > template.alignment.max_reproj_error_px:
        return PreprocessResult(False, f"No se reconoce la plantilla {template.template_id} v{template.version} en la imagen ({inliers} correspondencias "
                                       f"válidas, mínimo {template.alignment.min_inliers}; error {err_full:.1f} px): puede ser otro tipo de formato, "
                                       "o una foto incompleta, muy inclinada o desenfocada", None, tr)

    S_r_inv = np.diag([1 / scale, 1 / scale, 1.0])
    H_full = S_r_inv @ H @ S_t
    det = np.linalg.det(H_full[:2, :2])
    # area scale photo -> reference: ~1 for a scan; larger for a photo where the sheet fills part of the frame
    if not (float(cfg.get("min_scale_det", 0.4)) < det < float(cfg.get("max_scale_det", 2.5))):
        return PreprocessResult(False, f"Transformación implausible (escala {det:.2f})", None, tr)
    oriented_bgr = img if rot == 0 else cv2.rotate(img, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rot])

    def warp(H: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(oriented_bgr, H, (template.width, template.height), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))

    aligned = warp(H_full)
    # stage 2: local refinement with the template anchors (table and header corners), coarse search then fine
    from perfo.application.segment import check_alignment  # noqa: PLC0415

    searches = [int(s) for s in cfg.get("refine_search_px", [0])]  # 0: the alignment tolerance window
    current = check_alignment(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), template, reference_path, searches[0] or None)
    tr["residual_before_refine_px"] = current["max_offset_px"]
    for i, search in enumerate(searches):
        if i:
            current = check_alignment(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), template, reference_path, search or None)
        good = [a for a in current["anchors"] if a["score"] > 0.5]
        if len(good) < 6 or current["max_offset_px"] <= 1:
            continue
        src = np.float32([[a["anchor"][0] + a["dx"], a["anchor"][1] + a["dy"]] for a in good])
        dst = np.float32([a["anchor"] for a in good])
        H_ref, _ = cv2.findHomography(src, dst, 0)
        if H_ref is None:
            continue
        refined = warp(H_ref @ H_full)
        after = check_alignment(cv2.cvtColor(refined, cv2.COLOR_BGR2GRAY), template, reference_path, search or None)
        if after["max_offset_px"] < current["max_offset_px"]:
            aligned, H_full = refined, H_ref @ H_full
            tr["refined_by_anchors"] = True
            tr["residual_after_refine_px"] = after["max_offset_px"]
    if cfg.get("local_warp"):
        aligned = _local_warp(aligned, template, reference_path, cfg, tr)
    tr["homography"] = [[round(float(v), 8) for v in row] for row in H_full]
    return PreprocessResult(True, None, aligned, tr)


def _local_warp(aligned: np.ndarray, template: Template, reference_path: str, cfg: dict[str, Any], tr: dict[str, Any]) -> np.ndarray:
    """Stage 3 (photos): a smooth displacement field interpolated between the anchors corrects sheet curvature.

    Anchors with a weak match or an offset inconsistent with their neighbours are left out (occlusion, page edge).
    """
    from perfo.application.segment import check_alignment  # noqa: PLC0415

    before = check_alignment(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), template, reference_path, int(cfg.get("local_warp_search_px", 30)))
    max_px = float(cfg.get("local_warp_max_px", 25))
    cand = [a for a in before["anchors"] if a["score"] > 0.5 and max(abs(a["dx"]), abs(a["dy"])) <= max_px]
    if len(cand) < 8 or before["max_offset_px"] <= 1:
        return aligned
    pts = np.float32([a["anchor"] for a in cand])
    off = np.float32([[a["dx"], a["dy"]] for a in cand])
    keep = []
    for i in range(len(cand)):
        d = np.linalg.norm(pts - pts[i], axis=1)
        near = np.argsort(d)[1:5]
        keep.append(np.abs(off[i] - np.median(off[near], axis=0)).max() <= float(cfg.get("local_warp_outlier_px", 6)))
    pts, off = pts[np.array(keep)], off[np.array(keep)]
    if len(pts) < 8:
        return aligned
    h, w = aligned.shape[:2]
    step = 16
    gy, gx = np.mgrid[0:h:step, 0:w:step].astype(np.float32)
    d2 = (gx[..., None] - pts[:, 0]) ** 2 + (gy[..., None] - pts[:, 1]) ** 2
    wgt = 1.0 / (d2 + 400.0)
    field = [cv2.resize((wgt * off[:, k]).sum(-1) / wgt.sum(-1), (w, h), interpolation=cv2.INTER_LINEAR) for k in (0, 1)]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    warped = cv2.remap(aligned, xs + field[0], ys + field[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    after = check_alignment(cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY), template, reference_path)
    tr["local_warp"] = {"anchors_used": int(len(pts)), "residual_before_px": before["max_offset_px"], "residual_after_px": after["max_offset_px"]}
    return warped if after["max_offset_px"] <= before["max_offset_px"] else aligned
