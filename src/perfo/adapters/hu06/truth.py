"""Ground truth of the generated physical forms (verdad/*.json) -> double-entry reference CSV.

Each JSON lists the handwritten texts with their box on the flat reference page ({caja, texto, tachado}). A text
belongs to the template field that covers most of its box; struck-out texts (tachado) are not the value of the
field. The output has the columns of ReferenceGroundTruth: archivo, campo, valor, texto, operador.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from perfo.domain.fields import FieldType, Template
from perfo.domain.parsing import parse_field


def _overlap(box: list[float], cell: tuple[int, int, int, int]) -> float:
    bx0, by0, bx1, by1 = box
    x0, y0, x1, y1 = cell
    area = max(1e-6, (bx1 - bx0) * (by1 - by0))
    ix = max(0.0, min(bx1, x1) - max(bx0, x0))
    iy = max(0.0, min(by1, y1) - max(by0, y0))
    return ix * iy / area


def boxes_to_reference(template: Template, truth_dir: str | Path, images: dict[str, str], code_sets: dict[str, dict],
                       min_overlap: float = 0.5) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """images: JSON stem -> image file name (the `archivo` key that ingestion stores as form id)."""
    specs = [(s, template.box_px(s)) for s in template.prioritized()]
    rows: list[dict[str, str]] = []
    stats: Counter = Counter()
    missing_images: list[str] = []
    for path in sorted(Path(truth_dir).glob("*.json")):
        archivo = images.get(path.stem)
        if archivo is None:
            missing_images.append(path.stem)
            continue
        stats["formatos"] += 1
        per_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in json.loads(path.read_text(encoding="utf-8")):
            stats["textos"] += 1
            stats["tachados"] += bool(e.get("tachado"))
            spec, frac = max(((s, _overlap(e["caja"], box)) for s, box in specs), key=lambda t: t[1])
            if frac < min_overlap:
                stats["fuera_de_campos_priorizados"] += 1
                continue
            per_field[spec.id].append(e)
        for spec, _ in specs:
            entries = per_field.get(spec.id)
            if not entries:
                continue
            valid = sorted((e for e in entries if not e.get("tachado")), key=lambda e: (e["caja"][1] // 20, e["caja"][0]))
            if len(entries) > len(valid):
                stats["campos_con_tachado"] += 1
            if not valid:
                stats["campos_solo_tachado"] += 1  # the writer crossed out the only text: the field is empty
                continue
            text = " ".join(" ".join(e["texto"].split()) for e in valid)
            if spec.type == FieldType.FREE_TEXT:
                value = text
            else:
                # no shift in the truth (the shift is a check box): ambiguous 12 h times keep their literal reading
                value = parse_field(spec, text, None, code_sets).value or ""
            stats["campos"] += 1
            rows.append({"archivo": archivo, "campo": spec.id, "valor": value, "texto": text, "operador": ""})
    stats_out: dict[str, Any] = dict(stats)
    if missing_images:
        stats_out["sin_imagen"] = missing_images
    return rows, stats_out
