"""Import of the HU06 template (physical long-hole drilling form) into the perfo template format.

HU06 measured the real form layout on a flat reference page (referencia_plana.png) and froze the fields prioritized
in HU01 with pixel boxes on that page; tables are given as the box of row 1, the row step and the number of rows.
The importer keeps those coordinates, maps each field to the perfo field ids (so business rules, time typing by
shift and validation apply unchanged) and records the HU01 matrix id and the HU06 id for traceability.
"""

from __future__ import annotations

from typing import Any

# HU06 header fields -> perfo field id and typing
HEADER: dict[str, tuple[str, dict[str, Any]]] = {
    "cab.fecha": ("header.date", {"type": "date"}),
    # check boxes (Día / Noche / C/Guardia): TrOCR reads the printed labels, so the validator always confirms it
    "cab.turno": ("header.shift", {"type": "code", "code_set": "shift"}),
    "cab.guardia": ("header.crew", {"type": "code"}),
    "cab.cod_equipo": ("header.equipment", {"type": "code"}),
    "cab.operador": ("header.operator", {"type": "free_text"}),
    "cab.hor_motor_ini": ("header.engine_hm_start", {"type": "numeric", "subtype": "decimal"}),
    "cab.hor_motor_fin": ("header.engine_hm_end", {"type": "numeric", "subtype": "decimal"}),
    "cab.hor_perc_ini": ("header.percussion_hm_start", {"type": "numeric", "subtype": "decimal"}),
    "cab.hor_perc_fin": ("header.percussion_hm_end", {"type": "numeric", "subtype": "decimal"}),
    "cab.estado_ini": ("header.initial_state", {"type": "free_text"}),
    "cie.ubicacion": ("closing.location", {"type": "free_text"}),
    "cie.estado_fin": ("closing.final_state", {"type": "free_text"}),
    "cie.galones": ("closing.gallons", {"type": "numeric", "subtype": "decimal"}),
}

# HU06 table prefix -> perfo table id and title
TABLES = {"act": ("activities", "Actividades"), "dem": ("delays", "Demoras operativas"),
          "par": ("failures", "Paradas por fallas"), "ace": ("steel", "Control de aceros")}

# HU06 table column -> perfo column id and typing
COLUMNS: dict[str, tuple[str, dict[str, Any]]] = {
    "act.codigo": ("code", {"type": "code", "code_set": "activity_codes"}),
    "act.hora_ini": ("start", {"type": "time"}),
    "act.hora_fin": ("end", {"type": "time"}),
    "act.n_taladros": ("holes", {"type": "numeric", "subtype": "integer", "min": 0, "max": 99}),
    "act.n_barras": ("bars", {"type": "numeric", "subtype": "integer", "min": 0, "max": 99}),
    "act.longitud": ("length_m", {"type": "numeric", "subtype": "decimal", "min": 0, "max": 60}),
    "act.desv_m": ("deviation_m", {"type": "numeric", "subtype": "decimal"}),
    "act.desv_pct": ("deviation_pct", {"type": "numeric", "subtype": "decimal"}),
    "act.obs": ("notes", {"type": "free_text"}),
    "dem.labor": ("labor", {"type": "free_text"}),
    "dem.codigo": ("code", {"type": "code", "code_set": "delay_codes"}),
    "dem.hora_ini": ("start", {"type": "time"}),
    "dem.hora_fin": ("end", {"type": "time"}),
    "dem.obs": ("notes", {"type": "free_text"}),
    "par.labor": ("labor", {"type": "free_text"}),
    "par.codigo": ("code", {"type": "code", "code_set": "failure_codes"}),
    "par.hora_ini": ("start", {"type": "time"}),
    "par.hora_fin": ("end", {"type": "time"}),
    "par.h_total": ("total_hours", {"type": "numeric", "subtype": "decimal"}),
    "par.obs": ("notes", {"type": "free_text"}),
    "ace.brocas": ("bits", {"type": "numeric", "subtype": "integer"}),
    "ace.barras": ("rods", {"type": "numeric", "subtype": "integer"}),
    "ace.tubos": ("tube", {"type": "numeric", "subtype": "integer"}),
    "ace.shank": ("shank", {"type": "numeric", "subtype": "integer"}),
}

# printed legend of the physical form (column "Código de Falla")
LEGEND = {
    "actividad": {"101": "Perforación abanico (+)", "102": "Perforación abanico (-)", "103": "Perforación paralelo (+)",
                  "104": "Perforación paralelo (-)", "105": "Perforación horizontal", "106": "Desquinche", "107": "Perforación en realce"},
    "demora": {"201": "Reparto de guardia", "202": "Traslado a mina", "203": "Traslado a labor", "204": "Inspección de equipo",
               "205": "Falta de combustible", "206": "Esperando orden", "207": "Traslado de equipo", "208": "Inspección de labor / desate",
               "209": "Instalación de agua y energía", "210": "Esperando frente de trabajo", "211": "Falta de aceros",
               "212": "Recuperación de aceros", "213": "Ventilación deficiente", "214": "Voladura secundaria", "215": "Otros (especifique)",
               "216": "Falta de agua", "217": "Refrigerio"},
    "falla": {"301": "Falla mecánica", "302": "Falla eléctrica", "303": "Mantenimiento preventivo", "304": "Caída de tensión",
              "305": "Accidente / incidente", "306": "Otros (especifique)"},
}

# signature lines measured on the flat reference page (presence only, never transcribed)
SIGNATURES = [("signatures.operator", "Operador de equipo", 14, 363), ("signatures.shift_boss", "Jefe de guardia de mina", 633, 963),
              ("signatures.maintenance", "Responsable de mantenimiento", 1223, 1604),
              ("signatures.drilling_blasting", "Perforación y voladura", 1989, 2532)]
SIGNATURE_LINE_Y = 1493


def _norm(box: list[float], w: int, h: int) -> list[float]:
    x0, y0, x1, y1 = box
    return [round(x0 / w, 6), round(y0 / h, 6), round(x1 / w, 6), round(y1 / h, 6)]


def _required(hu06_required: bool, in_table: bool) -> str:
    if not hu06_required:
        return "no"
    return "if_row" if in_table else "always"


def frame_anchors(gray: Any, min_w: int = 150, min_h: int = 50) -> list[list[int]]:
    """Outer corners of the printed frames (tables and boxes) of the reference page, used as alignment anchors.

    Corners of the outer contour are distinctive; inner grid crossings repeat every row and mislead a wide search.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    ink = (gray < 150).astype(np.uint8)
    hor = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (80, 1)))
    ver = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 60)))
    grid = cv2.dilate(hor | ver, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pts: list[list[int]] = []
    for c in sorted(contours, key=lambda c: cv2.boundingRect(c)[:2]):
        _, _, w, h = cv2.boundingRect(c)
        if w >= min_w and h >= min_h:
            pts += [[int(x), int(y)] for x, y in cv2.approxPolyDP(c, 6, True).reshape(-1, 2)]
    return pts


def convert(hu06: dict[str, Any], reference_image: str, dpi: int = 250, anchors_px: list[list[int]] | None = None) -> dict[str, Any]:
    """HU06 template JSON -> perfo template definition (same coordinates, normalized to the reference page)."""
    ref = hu06["imagen_referencia"]
    w, h = int(ref["ancho"]), int(ref["alto"])
    fields: list[dict[str, Any]] = []
    tables: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    for c in hu06["campos"]:
        hid = c["id"]
        if hid in HEADER:
            fid, typing = HEADER[hid]
            fields.append({"id": fid, "block": fid.split(".")[0], "label": c["campo"], **typing, "box": _norm(c["caja"], w, h),
                           "priority": True, "required": _required(c["obligatorio"], False), "matrix_id": c["id_matriz"], "hu06_id": hid})
            continue
        prefix = hid.split(".")[0]
        if hid not in COLUMNS or prefix not in TABLES:
            unknown.append(hid)
            continue
        tid, title = TABLES[prefix]
        col_id, typing = COLUMNS[hid]
        x0, y0, x1, y1 = c["caja_fila1"]
        step, rows = float(c["paso_fila"]), int(c["filas"])
        t = tables.setdefault(tid, {"id": tid, "block": tid, "title": title, "rows": rows, "_x0": x0, "_x1": x1, "_y0": y0,
                                    "_step": step, "columns": []})
        if rows != t["rows"] or abs(step - t["_step"]) > 0.01 or abs(y0 - t["_y0"]) > 0.5:
            raise ValueError(f"{hid}: filas, paso o primera fila distintos de las demás columnas de la tabla {tid}")
        t["_x0"], t["_x1"] = min(t["_x0"], x0), max(t["_x1"], x1)
        t["columns"].append({"id": col_id, "label": c["campo"], **typing, "x0": round(x0 / w, 6), "x1": round(x1 / w, 6), "priority": True,
                             "required": _required(c["obligatorio"], True), "matrix_id": c["id_matriz"], "hu06_id": hid})
    if unknown:
        raise ValueError(f"Campos de HU06 sin correspondencia en el importador: {', '.join(unknown)}")
    table_defs = []
    for t in tables.values():
        # the box starts at row 1: its corners are line crossings of the printed grid (alignment anchors)
        y_end = t["_y0"] + t["rows"] * t["_step"]
        table_defs.append({"id": t["id"], "block": t["block"], "title": t["title"], "rows": t["rows"],
                           "box": _norm([t["_x0"], t["_y0"], t["_x1"], y_end], w, h), "header_height": 0.0,
                           "row_height": round(t["_step"] / h, 6), "columns": t["columns"]})
    signatures = [{"id": sid, "label": label, "box": _norm([x0, SIGNATURE_LINE_Y - 95, x1, SIGNATURE_LINE_Y + 32], w, h)}
                  for sid, label, x0, x1 in SIGNATURES]
    tol = hu06.get("tolerancias", {})
    return {
        "template_id": hu06["plantilla"],
        "version": "2.0",
        "valid_from": hu06.get("vigente_desde", ""),
        "description": "Formato físico de taladros largos (diseño medido en HU06 sobre la página plana de referencia; "
                       f"origen HU06 v{hu06.get('version')}: {hu06.get('estado', '')})",
        "source": {"hu06_template": hu06.get("plantilla"), "hu06_version": hu06.get("version"), "fields_origin": hu06.get("origen_campos"),
                   "excluded_hu01": hu06.get("excluidos_hu01", []), "hu06_tolerances": tol},
        "reference": {"width": w, "height": h, "dpi": dpi},
        "reference_image": reference_image,
        # photos: up to 10 % of the anchors may be occluded or fall outside the frame
        "alignment": {"tolerance_px": 12, "min_inliers": 40, "max_reproj_error_px": 4.0, "min_dpi": 200, "min_sharpness": 15.0,
                      "max_outlier_ratio": 0.1},
        "anchors": [[round(x / w, 6), round(y / h, 6)] for x, y in (anchors_px or [])],
        "code_sets": {
            "shift": {"values": ["D", "N", "C"], "synonyms": {"DIA": "D", "NOCHE": "N", "C/GUARDIA": "C", "CGUARDIA": "C"}},
            "activity_codes": {"master": "codigos", "kind": "actividad", "values": sorted(LEGEND["actividad"])},
            "delay_codes": {"master": "codigos", "kind": "demora", "values": sorted(LEGEND["demora"])},
            "failure_codes": {"master": "codigos", "kind": "falla", "values": sorted(LEGEND["falla"])},
        },
        "fields": fields,
        "tables": table_defs,
        "signatures": signatures,
        "static": {"codes": {"ACTIVIDADES": LEGEND["actividad"], "DEMORAS": LEGEND["demora"], "FALLAS": LEGEND["falla"]}},
    }


def legend_rows() -> list[dict[str, str]]:
    """Codes master (tipo, codigo, descripcion) taken from the legend printed on the form."""
    return [{"tipo": kind, "codigo": code, "descripcion": desc} for kind, codes in LEGEND.items() for code, desc in codes.items()]
