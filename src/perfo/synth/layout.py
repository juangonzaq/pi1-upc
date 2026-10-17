"""Builds template v1 (JSON) for the long-hole drilling report.

Geometry is defined in pixels of an A4 landscape page at 300 dpi and stored normalized.
The same definition drives the synthetic renderer and the segmentation, so both stay consistent.
"""

from __future__ import annotations

from typing import Any

W, H = 3508, 2480
M = 70

ACTIVITY_CODES = {
    "101": "PERFORACIÓN DE PRODUCCIÓN",
    "102": "REPASO DE TALADROS",
    "103": "PERFORACIÓN DE SLOT / CARA LIBRE",
    "104": "TRASLADO DE EQUIPO",
    "105": "POSICIONAMIENTO Y EMBOQUILLADO",
    "106": "LIMPIEZA DE TALADROS",
    "107": "OTRAS ACTIVIDADES",
}
DELAY_CODES = {
    "201": "CAMBIO DE GUARDIA",
    "202": "REFRIGERIO",
    "203": "FALTA DE AGUA",
    "204": "FALTA DE ENERGÍA",
    "205": "VENTILACIÓN DEFICIENTE",
    "206": "ESPERA DE TOPOGRAFÍA",
    "207": "ESPERA DE MARCADO",
    "208": "LABOR OCUPADA",
    "209": "TRÁNSITO DE EQUIPOS",
    "210": "CHARLA DE SEGURIDAD",
    "211": "INSPECCIÓN DE LABOR",
    "212": "DESATADO DE ROCAS",
    "213": "VOLADURA EN ZONA",
    "214": "FALTA DE ACEROS",
    "215": "FALTA DE OPERADOR",
    "216": "ABASTECIMIENTO DE COMBUSTIBLE",
    "217": "OTRAS DEMORAS",
}
FAILURE_CODES = {
    "301": "FALLA MECÁNICA",
    "302": "FALLA ELÉCTRICA",
    "303": "FALLA HIDRÁULICA",
    "304": "FALLA DE PERCUSIÓN",
    "305": "MANTENIMIENTO PREVENTIVO",
    "306": "OTRAS FALLAS",
}

# Prioritization matrix (HU01 / IE1): R relevance, F filling frequency, L expected legibility (1-5).
# score = 0.5 R + 0.3 F + 0.2 L; prioritized when score >= 3.5. Proposal pending PO validation.
PRIORITY: dict[str, tuple[int, int, int]] = {
    "header.date": (5, 5, 4),
    "header.shift": (5, 5, 5),
    "header.crew": (4, 5, 4),
    "header.equipment": (5, 5, 4),
    "header.stope": (5, 5, 3),
    "header.operator": (5, 5, 2),
    "header.initial_state": (2, 3, 2),
    "header.engine_hm_start": (5, 5, 4),
    "header.engine_hm_end": (5, 5, 4),
    "header.percussion_hm_start": (4, 5, 4),
    "header.percussion_hm_end": (4, 5, 4),
    "activities.activity_no": (3, 5, 5),
    "activities.code": (5, 5, 5),
    "activities.start": (5, 5, 3),
    "activities.end": (5, 5, 3),
    "activities.labor": (5, 4, 2),
    "activities.reamed_hole": (3, 2, 4),
    "activities.row_no": (4, 4, 4),
    "activities.holes": (5, 4, 4),
    "activities.bars": (4, 4, 4),
    "activities.length_m": (5, 4, 4),
    "activities.angle_planned": (4, 4, 4),
    "activities.angle_executed": (4, 4, 4),
    "activities.deviation_m": (4, 3, 3),
    "activities.deviation_pct": (4, 3, 3),
    "activities.notes": (2, 2, 1),
    "delays.labor": (4, 3, 3),
    "delays.code": (5, 3, 5),
    "delays.start": (5, 3, 3),
    "delays.end": (5, 3, 3),
    "delays.notes": (2, 2, 1),
    "failures.labor": (3, 2, 3),
    "failures.code": (5, 2, 5),
    "failures.start": (5, 2, 3),
    "failures.end": (5, 2, 3),
    "failures.total_hours": (4, 2, 4),
    "failures.notes": (2, 2, 1),
    "closing.location": (4, 4, 3),
    "closing.final_state": (3, 4, 3),
    "closing.gallons": (4, 4, 4),
    "closing.bits": (4, 4, 4),
    "closing.rods": (4, 4, 4),
    "closing.tube": (4, 3, 4),
    "closing.shank": (4, 4, 4),
    "closing.steel_notes": (2, 2, 1),
    "closing.general_notes": (2, 2, 1),
}
PRIORITY_CUTOFF = 3.5


def score(key: str) -> float:
    r, f, l = PRIORITY[key]
    return round(0.5 * r + 0.3 * f + 0.2 * l, 2)


def is_priority(key: str) -> bool:
    return score(key) >= PRIORITY_CUTOFF - 1e-9


def _n(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    return [round(x0 / W, 6), round(y0 / H, 6), round(x1 / W, 6), round(y1 / H, 6)]


def _field(fid: str, block: str, label: str, ftype: str, box_px: tuple[float, float, float, float], **kw: Any) -> dict[str, Any]:
    x0, y0, x1, y1 = box_px
    r, f, l = PRIORITY[fid]
    d = {
        "id": fid,
        "block": block,
        "label": label,
        "type": ftype,
        # value area: below the printed label strip
        "box": _n(x0 + 8, y0 + 40, x1 - 8, y1 - 6),
        "frame": _n(x0, y0, x1, y1),
        "priority": is_priority(fid),
        "score": {"R": r, "F": f, "L": l, "total": score(fid)},
        "required": kw.pop("required", "no"),
    }
    d.update(kw)
    return d


def _row_of_boxes(y0: float, y1: float, x_start: float, spec: list[tuple[str, str, str, float, dict]], block: str) -> list[dict]:
    out, x = [], x_start
    for fid, label, ftype, width, kw in spec:
        out.append(_field(fid, block, label, ftype, (x, y0, x + width, y1), **kw))
        x += width
    return out


def _columns(table: str, x_start: float, spec: list[tuple[str, str, str, float, dict]]) -> list[dict]:
    cols, x = [], x_start
    for cid, label, ftype, width, kw in spec:
        key = f"{table}.{cid}"
        r, f, l = PRIORITY[key]
        c = {
            "id": cid,
            "label": label,
            "type": ftype,
            "x0": round(x / W, 6),
            "x1": round((x + width) / W, 6),
            "priority": is_priority(key),
            "score": {"R": r, "F": f, "L": l, "total": score(key)},
            "required": kw.pop("required", "no"),
        }
        c.update(kw)
        cols.append(c)
        x += width
    return cols


def _table(tid: str, block: str, title: str, x0: float, y0: float, x1: float, rows: int, header_h: float, row_h: float, cols: list) -> dict:
    return {
        "id": tid,
        "block": block,
        "title": title,
        "rows": rows,
        "box": _n(x0, y0, x1, y0 + header_h + rows * row_h),
        "header_height": round(header_h / H, 6),
        "row_height": round(row_h / H, 6),
        "columns": _columns(tid, x0, cols),
    }


INT = {"subtype": "integer"}
DEC = {"subtype": "decimal"}


def build_template_v1() -> dict[str, Any]:
    fields: list[dict] = []
    req = {"required": "always"}
    fields += _row_of_boxes(170, 290, M, [
        ("header.date", "FECHA", "date", 420, dict(req)),
        ("header.shift", "TURNO (D/N)", "code", 300, {"code_set": "shift", **req}),
        ("header.crew", "GUARDIA", "code", 260, {"code_set": "crew", **req}),
        ("header.equipment", "EQUIPO", "master_ref", 560, {"master_type": "equipos", **req}),
        ("header.stope", "TAJO", "master_ref", 760, {"master_type": "labores", **req}),
        ("header.operator", "OPERADOR", "master_ref", 1068, {"master_type": "personal", **req}),
    ], "header")
    hm = {"min": 0, "max": 99999.9, **DEC, **req}
    fields += _row_of_boxes(300, 420, M, [
        ("header.engine_hm_start", "HORÓMETRO MOTOR INICIAL", "numeric", 560, dict(hm)),
        ("header.engine_hm_end", "HORÓMETRO MOTOR FINAL", "numeric", 560, dict(hm)),
        ("header.percussion_hm_start", "HORÓMETRO PERCUSIÓN INICIAL", "numeric", 560, dict(hm)),
        ("header.percussion_hm_end", "HORÓMETRO PERCUSIÓN FINAL", "numeric", 560, dict(hm)),
        ("header.initial_state", "ESTADO INICIAL DEL EQUIPO", "free_text", 1128, {}),
    ], "header")
    # closing block
    cx = 1330
    fields += _row_of_boxes(1680, 1780, cx, [
        ("closing.location", "UBICACIÓN FINAL DEL EQUIPO", "master_ref", 640, {"master_type": "labores"}),
        ("closing.final_state", "ESTADO FINAL", "free_text", 430, {}),
    ], "closing")
    steel = {"min": 0, "max": 20, **INT}
    fields += _row_of_boxes(1790, 1890, cx, [
        ("closing.gallons", "N.° GALONES", "numeric", 214, {"min": 0, "max": 300, **INT}),
        ("closing.bits", "BROCAS", "numeric", 214, dict(steel)),
        ("closing.rods", "BARRAS", "numeric", 214, dict(steel)),
        ("closing.tube", "TUBO", "numeric", 214, dict(steel)),
        ("closing.shank", "SHANK", "numeric", 214, dict(steel)),
    ], "closing")
    fields += _row_of_boxes(1900, 2000, cx, [("closing.steel_notes", "OBSERVACIONES CONTROL DE ACEROS", "free_text", 1070, {})], "closing")
    fields += _row_of_boxes(2010, 2110, cx, [("closing.general_notes", "OBSERVACIONES GENERALES", "free_text", 1070, {})], "closing")

    ifrow = {"required": "if_row"}
    activities = _table("activities", "activities", "ACTIVIDADES", M, 470, W - M, 22, 90, 48, [
        ("activity_no", "N.°", "numeric", 90, {"min": 1, "max": 22, **INT}),
        ("code", "CÓD. ACT.", "code", 140, {"code_set": "activity_codes", **ifrow}),
        ("start", "HORA/HORÓM. INICIAL", "time", 200, dict(ifrow)),
        ("end", "HORA/HORÓM. FINAL", "time", 200, dict(ifrow)),
        ("labor", "LABOR", "master_ref", 430, {"master_type": "labores", **ifrow}),
        ("reamed_hole", "TAL. REPASADO", "free_text", 140, {}),
        ("row_no", "N.° FILA", "numeric", 120, {"min": 1, "max": 60, **INT}),
        ("holes", "N.° TAL.", "numeric", 130, {"min": 1, "max": 40, **INT}),
        ("bars", "N.° BARRAS", "numeric", 130, {"min": 1, "max": 30, **INT}),
        ("length_m", "LONG. TAL. (m)", "numeric", 160, {"min": 1, "max": 30, **DEC}),
        ("angle_planned", "ÁNG. PROG.", "numeric", 150, {"min": 0, "max": 360, **DEC}),
        ("angle_executed", "ÁNG. EJEC.", "numeric", 150, {"min": 0, "max": 360, **DEC}),
        ("deviation_m", "DESV. (m)", "numeric", 150, {"min": 0, "max": 5, **DEC}),
        ("deviation_pct", "DESV. (%)", "numeric", 150, {"min": 0, "max": 50, **DEC}),
        ("notes", "OBSERVACIONES", "free_text", 1028, {}),
    ])
    delays = _table("delays", "delays", "DEMORAS OPERATIVAS", M, 1680, 1290, 6, 50, 48, [
        ("labor", "LABOR", "master_ref", 400, {"master_type": "labores", **ifrow}),
        ("code", "CÓDIGO", "code", 150, {"code_set": "delay_codes", **ifrow}),
        ("start", "HORA INICIAL", "time", 180, dict(ifrow)),
        ("end", "HORA FINAL", "time", 180, dict(ifrow)),
        ("notes", "OBSERVACIÓN", "free_text", 310, {}),
    ])
    failures = _table("failures", "failures", "PARADAS POR FALLAS Y REPARACIONES", M, 2065, 1290, 4, 50, 48, [
        ("labor", "LABOR", "master_ref", 330, {"master_type": "labores"}),
        ("code", "CÓDIGO", "code", 130, {"code_set": "failure_codes", **ifrow}),
        ("start", "HORA INICIAL", "time", 170, dict(ifrow)),
        ("end", "HORA FINAL", "time", 170, dict(ifrow)),
        ("total_hours", "HORAS TOTALES", "numeric", 150, {"min": 0, "max": 13, **DEC}),
        ("notes", "OBSERVACIÓN", "free_text", 270, {}),
    ])

    sig_w = 1070 / 4
    signatures = [
        {"id": f"signatures.{sid}", "label": label, "box": _n(cx + i * sig_w + 10, 2150, cx + (i + 1) * sig_w - 10, 2300)}
        for i, (sid, label) in enumerate([
            ("operator", "OPERADOR"),
            ("shift_boss", "JEFE DE GUARDIA MINA"),
            ("maintenance", "MANTENIMIENTO"),
            ("drilling_blasting", "PERF. Y VOLADURA"),
        ])
    ]

    return {
        "template_id": "reporte-perforacion-tl",
        "version": "1.0",
        "valid_from": "2026-10-14",
        "description": "Reporte de equipo de perforación de taladros largos (plantilla v1, geometría sintética A4 horizontal)",
        "reference": {"width": W, "height": H, "dpi": 300},
        "alignment": {"tolerance_px": 12, "min_inliers": 40, "max_reproj_error_px": 4.0, "min_dpi": 200, "min_sharpness": 15.0},
        "code_sets": {
            "shift": {"values": ["D", "N"], "synonyms": {"DIA": "D", "NOCHE": "N"}},
            "crew": {"values": ["A", "B", "C"]},
            "activity_codes": {"master": "codigos", "kind": "actividad", "values": list(ACTIVITY_CODES)},
            "delay_codes": {"master": "codigos", "kind": "demora", "values": list(DELAY_CODES)},
            "failure_codes": {"master": "codigos", "kind": "falla", "values": list(FAILURE_CODES)},
        },
        "fields": fields,
        "tables": [activities, delays, failures],
        "signatures": signatures,
        "static": {
            "title": "REPORTE DE EQUIPO DE PERFORACIÓN DE TALADROS LARGOS",
            "subtitle": "UNIDAD MINERA — FORMATO POR EQUIPO Y GUARDIA (PLANTILLA v1)",
            "legend_box": _n(2440, 1645, W - M, 2300),
            "rule_box": _n(2440, 2310, W - M, 2420),
            "rule_note": "% DESVIACIÓN = DESVIACIÓN (m) / LONGITUD DEL TALADRO (m) × 100. TODA DESVIACIÓN MAYOR DE 2 % DEBE CORREGIRSE.",
            "codes": {"ACTIVIDADES": ACTIVITY_CODES, "DEMORAS": DELAY_CODES, "FALLAS": FAILURE_CODES},
        },
    }


def priority_matrix_rows() -> list[dict[str, Any]]:
    tpl = build_template_v1()
    rows = []
    for f in tpl["fields"]:
        rows.append({"campo": f["id"], "bloque": f["block"], "etiqueta": f["label"], "tipo": f["type"],
                     "obligatorio": f["required"], **{k: f["score"][k] for k in "RFL"}, "puntaje": f["score"]["total"],
                     "priorizado": "sí" if f["priority"] else "no"})
    for t in tpl["tables"]:
        for c in t["columns"]:
            rows.append({"campo": f"{t['id']}.{c['id']}", "bloque": t["block"], "etiqueta": c["label"], "tipo": c["type"],
                         "obligatorio": c["required"], **{k: c["score"][k] for k in "RFL"}, "puntaje": c["score"]["total"],
                         "priorizado": "sí" if c["priority"] else "no"})
    order = {"header": 0, "activities": 1, "delays": 2, "failures": 3, "closing": 4}
    rows.sort(key=lambda r: (order[r["bloque"]], -r["puntaje"]))
    return rows
