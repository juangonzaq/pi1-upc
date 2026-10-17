"""Quality control and preprocessing: resolution, sharpness, orientation and perspective correction."""

from __future__ import annotations

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
    if dpi_eq < template.alignment.min_dpi:
        return PreprocessResult(False, f"Resolución insuficiente: {dpi_eq:.0f} ppp equivalentes (mínimo {template.alignment.min_dpi:.0f})", None, tr)

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
        return PreprocessResult(False, f"Perspectiva no corregible: {inliers} correspondencias válidas, error {err_full:.1f} px", None, tr)

    S_r_inv = np.diag([1 / scale, 1 / scale, 1.0])
    H_full = S_r_inv @ H @ S_t
    det = np.linalg.det(H_full[:2, :2])
    if not (0.4 < det < 2.5):
        return PreprocessResult(False, f"Transformación implausible (escala {det:.2f})", None, tr)
    oriented_bgr = img if rot == 0 else cv2.rotate(img, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rot])

    def warp(H: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(oriented_bgr, H, (template.width, template.height), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))

    aligned = warp(H_full)
    # stage 2: local refinement with the template anchors (table and header corners)
    from perfo.application.segment import check_alignment  # noqa: PLC0415

    before = check_alignment(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), template, reference_path)
    good = [a for a in before["anchors"] if a["score"] > 0.5]
    tr["residual_before_refine_px"] = before["max_offset_px"]
    if len(good) >= 6 and before["max_offset_px"] > 1:
        src = np.float32([[a["anchor"][0] + a["dx"], a["anchor"][1] + a["dy"]] for a in good])
        dst = np.float32([a["anchor"] for a in good])
        H_ref, _ = cv2.findHomography(src, dst, 0)
        if H_ref is not None:
            refined = warp(H_ref @ H_full)
            after = check_alignment(cv2.cvtColor(refined, cv2.COLOR_BGR2GRAY), template, reference_path)
            if after["max_offset_px"] < before["max_offset_px"]:
                aligned, H_full = refined, H_ref @ H_full
                tr["refined_by_anchors"] = True
                tr["residual_after_refine_px"] = after["max_offset_px"]
    tr["homography"] = [[round(float(v), 8) for v in row] for row in H_full]
    return PreprocessResult(True, None, aligned, tr)
