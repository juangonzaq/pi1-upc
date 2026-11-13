"""Real handwritten digits (MNIST) in place of font digits, so each operator's digits vary from one stroke to the next.

A handwriting font draws every "7" the same way; a person does not. Each synthetic operator gets a small fixed pool of
real samples per digit (a consistent hand with natural variation). Historical operators draw from the MNIST training
writers and new operators from the test writers, which are disjoint people, so "unseen operator" also means an unseen
hand. MNIST: Y. LeCun, C. Cortes, C. Burges, CC BY-SA 3.0; downloaded once into the gitignored data folder.
"""

from __future__ import annotations

import gzip
import random
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFont

MNIST_URL = "https://ossci-datasets.s3.amazonaws.com/mnist/"
FILES = {
    "train": ("train-images-idx3-ubyte.gz", "train-labels-idx1-ubyte.gz"),
    "test": ("t10k-images-idx3-ubyte.gz", "t10k-labels-idx1-ubyte.gz"),
}
SOURCE = "MNIST (LeCun, Cortes, Burges), CC BY-SA 3.0"
# mask contrast per stroke weight (HandStyle.weight 0/1/2): higher offset = thinner stroke
_WEIGHT = {0: (125, 2.6), 1: (90, 2.1), 2: (55, 1.8)}


def _idx(data: bytes) -> np.ndarray:
    dims = data[3]
    shape = tuple(int.from_bytes(data[4 + 4 * i : 8 + 4 * i], "big") for i in range(dims))
    return np.frombuffer(data, np.uint8, offset=4 + 4 * dims).reshape(shape)


def ensure_digit_bank(cache_dir: str | Path) -> Path | None:
    """Downloads MNIST once and stores it as one npz. Returns None without network (the generator falls back to fonts)."""
    target = Path(cache_dir) / "mnist_digits.npz"
    if target.exists():
        return target
    arrays: dict[str, np.ndarray] = {}
    try:
        for split, (img_name, lbl_name) in FILES.items():
            for name, key in ((img_name, f"{split}_images"), (lbl_name, f"{split}_labels")):
                with urllib.request.urlopen(MNIST_URL + name, timeout=60) as resp:  # noqa: S310 (fixed https host)
                    arrays[key] = _idx(gzip.decompress(resp.read()))
    except Exception:  # noqa: BLE001
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    tmp.replace(target)
    return target


@lru_cache(maxsize=2)
def _bank(path: str) -> dict[str, np.ndarray]:
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def operator_pools(bank_path: str | Path, split: str, rng: random.Random, per_digit: int) -> dict[str, list[int]]:
    """A fixed handful of real samples per digit for one operator (indices into the given MNIST split)."""
    labels = _bank(str(bank_path))[f"{split}_labels"]
    pools: dict[str, list[int]] = {}
    for d in range(10):
        idx = np.flatnonzero(labels == d)
        pools[str(d)] = sorted(rng.sample(range(len(idx)), k=per_digit))
        pools[str(d)] = [int(idx[i]) for i in pools[str(d)]]
    return pools


@dataclass(frozen=True)
class DigitHand:
    bank: str
    split: str
    pools: dict[str, list[int]]

    def glyph(self, ch: str, font: ImageFont.FreeTypeFont, ink: tuple[int, int, int], weight: int,
              rng: random.Random) -> tuple[Image.Image, float]:
        """RGBA patch of one real digit sized to the font's digit height, and its top offset from the text origin."""
        img = _bank(self.bank)[f"{self.split}_images"][rng.choice(self.pools[ch])]
        ys, xs = np.nonzero(img > 30)
        crop = img[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        _, top, _, bottom = font.getbbox("8")
        h = max(6, round((bottom - top) * rng.uniform(0.9, 1.18)))
        w = max(2, round(crop.shape[1] * h / crop.shape[0] * rng.uniform(0.9, 1.1)))
        mask = np.asarray(Image.fromarray(crop).resize((w, h), Image.Resampling.BICUBIC), np.float32)
        offset, gain = _WEIGHT.get(weight, _WEIGHT[1])
        alpha = np.clip((mask - offset) * gain, 0, 255).astype(np.uint8)
        patch = Image.new("RGBA", (w, h), (*ink, 0))
        patch.putalpha(Image.fromarray(alpha))
        return patch, float(bottom - h)
