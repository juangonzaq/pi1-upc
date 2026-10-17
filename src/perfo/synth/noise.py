"""Scan noise: slight rotation and perspective, scale, blur, stains, speckles, JPEG."""

from __future__ import annotations

import io
import random

import cv2
import numpy as np
from PIL import Image


def scan(page: Image.Image, rng: random.Random, strength: float = 1.0, bad: str | None = None) -> tuple[bytes, dict]:
    """Returns JPEG bytes and the applied parameters. `bad` produces uncorrectable scans for testing."""
    img = np.asarray(page.convert("RGB")).astype(np.float32)
    h, w = img.shape[:2]
    params: dict = {}

    # stains (coffee / dirt): soft brownish blobs
    for _ in range(rng.randint(0, 3)):
        mask = np.zeros((h, w), np.float32)
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        ax, ay = rng.randint(40, 220), rng.randint(30, 160)
        cv2.ellipse(mask, (cx, cy), (ax, ay), rng.uniform(0, 180), 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), rng.uniform(10, 25)) * rng.uniform(0.08, 0.22)
        tint = np.array([150, 120, 80], np.float32)
        img = img * (1 - mask[..., None]) + tint * mask[..., None]
    # speckles
    n_sp = int(rng.uniform(200, 1500) * strength)
    ys = np.array([rng.randrange(h) for _ in range(n_sp)])
    xs = np.array([rng.randrange(w) for _ in range(n_sp)])
    img[ys, xs] = rng.uniform(40, 120)

    # geometry: rotation + perspective + scale
    angle = rng.uniform(-1.6, 1.6) * strength
    jitter = 0.012 * strength
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + np.float32([[rng.uniform(-jitter, jitter) * w, rng.uniform(-jitter, jitter) * h] for _ in range(4)])
    M_p = cv2.getPerspectiveTransform(src, dst)
    R = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    R3 = np.vstack([R, [0, 0, 1]])
    scale = rng.uniform(0.96, 1.04)
    S = np.diag([scale, scale, 1.0])
    M = S @ R3 @ M_p
    out_w, out_h = int(w * scale), int(h * scale)
    img = cv2.warpPerspective(img, M, (out_w, out_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(238, 238, 236))
    params.update(angle=round(angle, 3), scale=round(scale, 4))

    # blur, illumination and sensor noise
    sigma = rng.uniform(0.0, 0.9) * strength
    if sigma > 0.15:
        img = cv2.GaussianBlur(img, (0, 0), sigma)
    gain = rng.uniform(0.94, 1.03)
    bias = rng.uniform(-8, 6)
    img = img * gain + bias
    grad = np.linspace(rng.uniform(-6, 0), rng.uniform(0, 6), out_w, dtype=np.float32)[None, :, None]
    img = img + grad
    img = img + np.random.default_rng(rng.randrange(1 << 30)).normal(0, rng.uniform(2.0, 5.0), img.shape).astype(np.float32)
    params.update(blur=round(sigma, 3))

    if bad == "low_res":
        small = cv2.resize(img, (out_w * 2 // 5, out_h * 2 // 5), interpolation=cv2.INTER_AREA)
        img = small
        params["bad"] = "low_res"
    elif bad == "cut":
        img = img[: int(out_h * 0.45), :]  # bottom half of the page missing
        params["bad"] = "cut"

    img = np.clip(img, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    q = rng.randint(86, 93)
    dpi = 300 if bad != "low_res" else 120
    Image.fromarray(img).save(buf, format="JPEG", quality=q, dpi=(dpi, dpi))
    params["jpeg_quality"] = q
    return buf.getvalue(), params
