"""Handwriting fonts (OFL, Google Fonts). Downloaded when there is network; otherwise system fallbacks."""

from __future__ import annotations

import urllib.request
from pathlib import Path

BASE_URL = "https://github.com/google/fonts/raw/main/"

HANDWRITING = {
    "Caveat": "ofl/caveat/Caveat%5Bwght%5D.ttf",
    "Kalam": "ofl/kalam/Kalam-Regular.ttf",
    "PatrickHand": "ofl/patrickhand/PatrickHand-Regular.ttf",
    "IndieFlower": "ofl/indieflower/IndieFlower-Regular.ttf",
    "ShadowsIntoLight": "ofl/shadowsintolight/ShadowsIntoLight.ttf",
    "GochiHand": "ofl/gochihand/GochiHand-Regular.ttf",
    "ReenieBeanie": "ofl/reeniebeanie/ReenieBeanie.ttf",
    "ArchitectsDaughter": "ofl/architectsdaughter/ArchitectsDaughter-Regular.ttf",
    "CoveredByYourGrace": "ofl/coveredbyyourgrace/CoveredByYourGrace.ttf",
    "Handlee": "ofl/handlee/Handlee-Regular.ttf",
    "NothingYouCouldDo": "ofl/nothingyoucoulddo/NothingYouCouldDo.ttf",
    "AnnieUseYourTelescope": "ofl/annieuseyourtelescope/AnnieUseYourTelescope-Regular.ttf",
}
PRINTED = {"ArchivoNarrow": "ofl/archivonarrow/ArchivoNarrow%5Bwght%5D.ttf"}

SYSTEM_FALLBACKS = [
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "/System/Library/Fonts/Noteworthy.ttc",
    "/System/Library/Fonts/MarkerFelt.ttc",
    "/System/Library/Fonts/Supplemental/Chalkboard.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
]
SYSTEM_PRINTED = [
    "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _download(url: str, target: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https host)
            data = resp.read()
        if len(data) < 10_000:
            return False
        target.write_bytes(data)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_fonts(cache_dir: str | Path) -> dict[str, list[str]]:
    """Returns {"handwriting": [...paths], "printed": [...paths], "source": "google-fonts|system"}."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    hand: list[str] = []
    for name, rel in HANDWRITING.items():
        path = cache / f"{name}.ttf"
        if path.exists() or _download(BASE_URL + rel, path):
            hand.append(str(path))
    printed: list[str] = []
    for name, rel in PRINTED.items():
        path = cache / f"{name}.ttf"
        if path.exists() or _download(BASE_URL + rel, path):
            printed.append(str(path))
    source = "google-fonts"
    if len(hand) < 4:
        source = "system"
        hand += [p for p in SYSTEM_FALLBACKS if Path(p).exists()]
    if not printed:
        printed = [p for p in SYSTEM_PRINTED if Path(p).exists()]
    if not hand:
        raise RuntimeError("No hay fuentes manuscritas disponibles (sin red y sin fuentes del sistema)")
    return {"handwriting": sorted(set(hand), key=hand.index), "printed": printed, "source": [source]}
