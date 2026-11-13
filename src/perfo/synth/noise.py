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
        small = cv2.resize(img, (out_w // 4, out_h // 4), interpolation=cv2.INTER_AREA)  # 75 ppp: below the 80 ppp floor
        img = small
        params["bad"] = "low_res"
    elif bad == "cut":
        img = img[: int(out_h * 0.45), :]  # bottom half of the page missing
        params["bad"] = "cut"

    img = np.clip(img, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    q = rng.randint(86, 93)
    dpi = 300 if bad != "low_res" else 75
    Image.fromarray(img).save(buf, format="JPEG", quality=q, dpi=(dpi, dpi))
    params["jpeg_quality"] = q
    return buf.getvalue(), params


def phone(page: Image.Image, rng: random.Random, ppp: tuple[float, float] = (105.0, 220.0), ref_dpi: float = 300.0) -> tuple[bytes, dict]:
    """A phone photo of the sheet instead of a scanner pass: the conditions of the real field photos (~110 ppp).

    Uneven light and a soft shadow (a hand or the phone itself), colour cast, a slightly curved sheet, stronger
    perspective, the resolution of a phone photo, motion blur, the phone's own sharpening and heavier JPEG. The sheet
    still fills the frame (no table around it): the base pipeline expects a page, as a scanner gives.
    """
    img = np.asarray(page.convert("RGB")).astype(np.float32)
    h, w = img.shape[:2]
    gen = np.random.default_rng(rng.randrange(1 << 30))
    params: dict = {"capture": "phone"}

    # curved sheet: smooth displacement (a few px at 300 ppp)
    amp = rng.uniform(1.0, 3.0)
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
    fx, fy = rng.uniform(0.6, 1.6), rng.uniform(0.6, 1.6)
    ph1, ph2 = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
    map_x = gx + amp * np.sin(gy / h * np.pi * fy + ph1)
    map_y = gy + amp * np.sin(gx / w * np.pi * fx + ph2)
    img = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    del gy, gx, map_x, map_y

    # light: gradient across the sheet, vignette and, often, a soft shadow from one side
    yy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    a, b = rng.uniform(-0.12, 0.12), rng.uniform(-0.12, 0.12)
    light = 1.0 + a * xx + b * yy - rng.uniform(0.04, 0.14) * (xx**2 + yy**2)
    if rng.random() < 0.6:
        mask = np.zeros((h, w), np.float32)
        side = rng.choice(["left", "right", "top", "bottom"])
        depth = rng.uniform(0.15, 0.45)
        if side in ("left", "right"):
            x_edge = int(w * depth) if side == "left" else int(w * (1 - depth))
            jitter = int(w * 0.08)
            poly = np.array([[0 if side == "left" else w, 0], [x_edge + rng.randint(-jitter, jitter), 0],
                             [x_edge + rng.randint(-jitter, jitter), h], [0 if side == "left" else w, h]], np.int32)
        else:
            y_edge = int(h * depth) if side == "top" else int(h * (1 - depth))
            jitter = int(h * 0.08)
            poly = np.array([[0, 0 if side == "top" else h], [0, y_edge + rng.randint(-jitter, jitter)],
                             [w, y_edge + rng.randint(-jitter, jitter)], [w, 0 if side == "top" else h]], np.int32)
        cv2.fillPoly(mask, [poly], 1.0)
        mask = cv2.GaussianBlur(mask, (0, 0), rng.uniform(25, 80))
        strength = rng.uniform(0.08, 0.26)  # stronger shadows defeat the template alignment (see README)
        light = light * (1 - strength * mask)
        params["shadow"] = {"side": side, "strength": round(strength, 3)}
    img = img * light[..., None]
    cast = np.array([rng.uniform(0.97, 1.05), rng.uniform(0.97, 1.02), rng.uniform(0.9, 1.0)], np.float32)  # warm indoor light
    img = img * cast

    # geometry: stronger tilt and perspective than a scanner
    angle = rng.uniform(-3.0, 3.0)
    jitter = 0.03
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + np.float32([[rng.uniform(-jitter, jitter) * w, rng.uniform(-jitter, jitter) * h] for _ in range(4)])
    M = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0), [0, 0, 1]]) @ cv2.getPerspectiveTransform(src, dst)
    img = cv2.warpPerspective(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # phone resolution
    target = rng.uniform(*ppp)
    s = target / ref_dpi
    img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    params.update(angle=round(angle, 3), ppp=round(target, 1))

    # hand shake, sensor noise, then the phone's sharpening
    if rng.random() < 0.35:
        k = rng.choice([3, 3, 5])
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2, :] = 1.0 / k
        kernel = cv2.warpAffine(kernel, cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), rng.uniform(0, 180), 1.0), (k, k))
        img = cv2.filter2D(img, -1, kernel / max(kernel.sum(), 1e-6))
        params["motion_blur"] = k
    img = img + gen.normal(0, rng.uniform(3.0, 8.0), img.shape).astype(np.float32)
    if rng.random() < 0.6:
        blur = cv2.GaussianBlur(img, (0, 0), 1.2)
        amount = rng.uniform(0.3, 0.9)
        img = img + amount * (img - blur)
        params["sharpen"] = round(amount, 2)

    img = np.clip(img, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    q = rng.randint(55, 85)
    Image.fromarray(img).save(buf, format="JPEG", quality=q, dpi=(72, 72))
    params["jpeg_quality"] = q
    return buf.getvalue(), params
