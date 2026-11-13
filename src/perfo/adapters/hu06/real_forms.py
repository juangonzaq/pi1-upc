"""Templates of the real printed forms, built from the only two field photos available (no blank scan exists).

Each photo is flattened onto a 2912×1776 page: a perspective correction from the corners of its main table, then the
ruled lines of that table, measured in columns without writing, are made straight. Pen ink is removed so that the
reference holds the printed form only. The flat pages stay out of git (they come from real photos); the template
definitions keep the same field ids as template v2 wherever the concept is the same, so rules and validation apply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

W, H = 2912, 1776
DPI = 250  # same page and scale as template v2


@dataclass(frozen=True)
class Flattening:
    corners: list[list[float]]  # main table corners on the photo (clockwise from the top left)
    target: list[list[float]]  # the same corners on the flat page
    strips: list[int]  # x of the columns without writing where ruled lines are measured
    y_range: tuple[int, int]  # band holding the lines of the main table
    rows: int
    bands: list[int]  # y of the bands without writing where column lines are measured
    fold: tuple[int, int, float] | None = None  # a crease drops the bottom right of the sheet: x where it starts and ends, drop in px


def _peaks(profile: Any, threshold: float, min_gap: int) -> list[int]:
    import numpy as np  # noqa: PLC0415

    found: list[int] = []
    for i in np.argsort(-profile):
        if profile[i] < threshold:
            break
        if all(abs(int(i) - j) >= min_gap for j in found):
            found.append(int(i))
    return sorted(found)


def _regular_run(positions: list[int], n: int) -> Any:
    """The n consecutive lines with the most regular step: the ruled lines of the table."""
    import numpy as np  # noqa: PLC0415

    best, run = None, None
    for i in range(len(positions) - n + 1):
        r = np.array(positions[i : i + n])
        d = np.diff(r)
        score = d.std() + abs(np.median(d) - d.mean())
        if best is None or score < best:
            best, run = score, r
    if run is None:
        raise ValueError(f"Se esperaban {n} líneas de la tabla y se hallaron {len(positions)}")
    return run


def _straighten_rows(page: Any, spec: Flattening) -> Any:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    bright = page.max(2)  # the brightest channel keeps black lines dark and fades blue and red ink
    y0, y1 = spec.y_range
    lines, strips = [], []
    for x in spec.strips:
        band = (255 - bright[y0:y1, x - 35 : x + 35]).astype(float)
        band = band - cv2.GaussianBlur(band, (0, 0), 9)
        profile = np.convolve(np.percentile(band, 60, axis=1), np.ones(3) / 3, "same")
        profile -= np.median(profile)
        found = [p + y0 for p in _peaks(profile, np.percentile(profile, 70), 16)]
        if len(found) > spec.rows:
            lines.append(_regular_run(found, spec.rows + 1))
            strips.append(x)
    measured = np.array(lines, float)
    row = np.arange(spec.rows + 1)
    for i, line in enumerate(measured):  # a stroke crossing a line must not bend it
        fit = np.polyval(np.polyfit(row, line, 3), row)
        measured[i] = np.where(np.abs(line - fit) > 3, fit, line)
    target = np.linspace(measured[:, 0].mean(), measured[:, -1].mean(), spec.rows + 1)
    per_line = np.array([np.interp(np.arange(W), strips, measured[:, i] - target[i]) for i in row])  # the paper waves: no single curve fits it
    per_line = np.vstack([per_line[0], per_line, per_line[-1]])
    knots = np.r_[0, target, H - 1]
    dy = np.zeros((H, W), np.float32)
    for col in range(W):
        dy[:, col] = np.interp(np.arange(H), knots, per_line[:, col])
    if spec.fold:
        x_from, x_to, drop = spec.fold
        bottom = np.clip((np.arange(H, dtype=np.float32) - target[-1] - 200) / 100, 0, 1)[:, None]
        dy += bottom * np.clip((np.arange(W, dtype=np.float32) - x_from) / (x_to - x_from), 0, 1)[None, :] * drop
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    return cv2.remap(page, xs, ys + dy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def _straighten_columns(page: Any, spec: Flattening) -> Any:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    bright = page.max(2)
    bands = []
    for y in spec.bands:
        band = bright[y - 55 : y + 55]
        ink = (cv2.GaussianBlur(band, (0, 0), 15).astype(int) - band > 14).astype(np.uint8)
        ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 22)))
        bands.append((y, _peaks(ink.sum(0).astype(float), 33.0, 12)))
    reference = max(bands, key=lambda b: len(b[1]))[1]
    mid_y, mids, slopes = float(np.mean(spec.bands)), [], []
    for x in reference:
        near = [(y, min(found, key=lambda v: abs(v - x))) for y, found in bands if found]
        pts = [(y, v) for y, v in near if abs(v - x) <= 38]
        if len(pts) >= 3:
            slope, offset = np.polyfit([p[0] for p in pts], [p[1] for p in pts], 1)
            mids.append(offset + slope * mid_y)
            slopes.append(slope)
    order = np.argsort(mids)
    slope_at = np.interp(np.arange(W), np.array(mids)[order], np.array(slopes)[order])
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    dx = (ys - mid_y) * slope_at[None, :].astype(np.float32)
    return cv2.remap(page, xs + dx, ys, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def flatten(photo: Path, spec: Flattening) -> Any:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    img = cv2.imread(str(photo))
    if img is None:
        raise FileNotFoundError(photo)
    M = cv2.getPerspectiveTransform(np.float32(spec.corners), np.float32(spec.target))
    page = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    return _straighten_columns(_straighten_rows(page, spec), spec)


def remove_pen(page: Any) -> Any:
    """Paints over blue and red ink: the reference must hold the printed form, not what one operator wrote."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    hsv = cv2.cvtColor(page, cv2.COLOR_BGR2HSV)
    h, s = hsv[..., 0], hsv[..., 1]
    pen = (((h > 95) & (h < 150)) | (h < 8) | (h > 165)) & (s > 55)
    return cv2.inpaint(page, cv2.dilate(pen.astype(np.uint8) * 255, np.ones((5, 5), np.uint8)), 5, cv2.INPAINT_TELEA)


def _box(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    return [round(x0 / W, 6), round(y0 / H, 6), round(x1 / W, 6), round(y1 / H, 6)]


def _table(tid: str, title: str, x_edges: tuple[int, int], y0: float, step: float, rows: int, columns: list[dict[str, Any]]) -> dict[str, Any]:
    cols = [{**c, "x0": round(c["x0"] / W, 6), "x1": round(c["x1"] / W, 6), "priority": True, "required": c.get("required", "no")} for c in columns]
    return {"id": tid, "block": tid, "title": title, "rows": rows, "box": _box(x_edges[0], y0, x_edges[1], y0 + rows * step), "header_height": 0.0,
            "row_height": round(step / H, 6), "columns": cols}


def _field(fid: str, label: str, ftype: str, box: tuple[float, float, float, float], required: str = "no", **kw: Any) -> dict[str, Any]:
    return {"id": fid, "block": fid.split(".")[0], "label": label, "type": ftype, "box": _box(*box), "priority": True, "required": required, **kw}


def _signature(sid: str, label: str, x0: float, x1: float, line_y: float) -> dict[str, Any]:
    return {"id": sid, "label": label, "box": _box(x0, line_y - 70, x1, line_y + 20)}


def _corners(tables: list[dict[str, Any]]) -> list[list[float]]:
    return [[t["box"][i], t["box"][j]] for t in tables for i, j in ((0, 1), (2, 1), (0, 3), (2, 3))]


TIME = {"type": "time"}
DEC = {"type": "numeric", "subtype": "decimal"}
INT = {"type": "numeric", "subtype": "integer"}
TEXT = {"type": "free_text"}
ALIGNMENT = {"tolerance_px": 14, "min_inliers": 25, "max_reproj_error_px": 4.0, "min_dpi": 200, "min_sharpness": 15.0, "max_outlier_ratio": 0.34}


def long_hole() -> tuple[str, Flattening, dict[str, Any]]:
    """Real long-hole form (ejemplo_03): same concepts as template v2, measured on the paper."""
    k = 1.456
    spec = Flattening(corners=[[124, 166], [1017, 163], [1052, 425], [98, 429]], target=[[124 * k, 219 * k], [1590 * k, 219 * k], [1590 * k, 647 * k], [124 * k, 647 * k]],
                      strips=[300, 440, 720, 930, 1210, 1420, 1700, 1910, 2120, 2260], y_range=(385, 947), rows=22, bands=[440, 600, 760, 900], fold=(2127, 2260, 25.0))
    activities = _table("activities", "Actividades", (260, 2310), 400, 24.41, 22, [
        {"id": "code", "label": "Código de actividad", "type": "code", "code_set": "activity_codes", "x0": 260, "x1": 398, "required": "if_row"},
        {"id": "start", "label": "Hora inicial", **TIME, "x0": 398, "x1": 543, "required": "if_row"},
        {"id": "end", "label": "Hora final", **TIME, "x0": 543, "x1": 652, "required": "if_row"},
        {"id": "holes", "label": "N.° de taladros", **INT, "min": 0, "max": 99, "x0": 1227, "x1": 1331},
        {"id": "bars", "label": "N.° de barras usadas", **INT, "min": 0, "max": 99, "x0": 1331, "x1": 1416},
        {"id": "length_m", "label": "Longitud del taladro (m)", **DEC, "min": 0, "max": 60, "x0": 1416, "x1": 1516},
        {"id": "deviation_m", "label": "Desviación (m)", **DEC, "x0": 1689, "x1": 1844},
        {"id": "deviation_pct", "label": "Desviación (%)", **DEC, "x0": 1844, "x1": 1968},
        {"id": "notes", "label": "Observaciones", **TEXT, "x0": 1968, "x1": 2310}])
    delays = _table("delays", "Demoras operativas", (164, 1225), 1025, 24.8, 8, [
        {"id": "labor", "label": "Labor", **TEXT, "x0": 164, "x1": 350},
        {"id": "code", "label": "Código de demora", "type": "code", "code_set": "delay_codes", "x0": 350, "x1": 461, "required": "if_row"},
        {"id": "start", "label": "Hora inicial", **TIME, "x0": 461, "x1": 581, "required": "if_row"},
        {"id": "end", "label": "Hora final", **TIME, "x0": 581, "x1": 712, "required": "if_row"},
        {"id": "notes", "label": "Observación", **TEXT, "x0": 712, "x1": 1225}])
    failures = _table("failures", "Paradas por fallas", (1225, 2180), 1026, 25.2, 8, [
        {"id": "labor", "label": "Labor", **TEXT, "x0": 1225, "x1": 1351},
        {"id": "code", "label": "Código de falla", "type": "code", "code_set": "failure_codes", "x0": 1351, "x1": 1490, "required": "if_row"},
        {"id": "start", "label": "Hora inicial", **TIME, "x0": 1490, "x1": 1637, "required": "if_row"},
        {"id": "end", "label": "Hora final", **TIME, "x0": 1637, "x1": 1844, "required": "if_row"},
        {"id": "total_hours", "label": "Horas totales", **DEC, "x0": 1844, "x1": 1968},
        {"id": "notes", "label": "Observación", **TEXT, "x0": 1968, "x1": 2180}])
    steel = _table("steel", "Control de aceros", (1846, 2359), 1307, 29.5, 3, [
        {"id": "bits", "label": "N.° de brocas", **INT, "x0": 1846, "x1": 1971},
        {"id": "rods", "label": "N.° de barras", **INT, "x0": 1971, "x1": 2076},
        {"id": "tube", "label": "Tubo Tac", **INT, "x0": 2076, "x1": 2182},
        {"id": "shank", "label": "Shank", **INT, "x0": 2182, "x1": 2359}])
    tables = [activities, delays, failures, steel]
    definition = {
        "template_id": "reporte-perforacion-tl-real", "version": "real-tl-1.0", "valid_from": "2026-10-30",
        "description": "Formato real de taladros largos, medido sobre la foto ejemplo_03 aplanada (sin escaneo en blanco disponible)",
        "fields": [
            _field("header.date", "Fecha", "date", (257, 243, 590, 284), "always"),
            _field("header.shift", "Turno", "code", (257, 284, 590, 314), "always", code_set="shift"),
            _field("header.crew", "Guardia", "code", (2235, 82, 2397, 140), "always"),
            _field("header.equipment", "Código del equipo", "code", (1956, 15, 2133, 60), "always"),
            _field("header.operator", "Operador de equipo", "free_text", (1950, 128, 2350, 195), "always"),
            _field("header.engine_hm_start", "Horómetro de motor inicial", "numeric", (1228, 262, 1365, 290), subtype="decimal"),
            _field("header.engine_hm_end", "Horómetro de motor final", "numeric", (1228, 290, 1365, 319), subtype="decimal"),
            _field("header.percussion_hm_start", "Horómetro de percusión inicial", "numeric", (1365, 262, 1640, 290), subtype="decimal"),
            _field("header.percussion_hm_end", "Horómetro de percusión final", "numeric", (1365, 290, 1640, 319), subtype="decimal"),
            _field("header.initial_state", "Estado inicial del equipo", "free_text", (1990, 76, 2155, 115)),
            _field("closing.location", "Ubicación del equipo", "free_text", (441, 1323, 794, 1360)),
            _field("closing.final_state", "Estado final del equipo", "free_text", (1081, 1330, 1235, 1365)),
            _field("closing.gallons", "N.° de galones", "numeric", (1324, 1330, 1735, 1365), subtype="decimal"),
        ],
        "tables": tables,
        "signatures": [_signature("signatures.operator", "Operador de equipo", 154, 470, 1429), _signature("signatures.shift_boss", "Jefe de guardia de mina", 709, 1000, 1444),
                       _signature("signatures.maintenance", "Responsable de mantenimiento", 1228, 1515, 1444), _signature("signatures.drilling_blasting", "Perforación y voladura", 1897, 2353, 1462)],
    }
    return "Formato_fisico_ejemplo_03.jpeg", spec, definition


JACKLEG_LEGEND = {
    "101": "Perforación horizontal", "102": "Perforación desquinche", "103": "Perforación en breasting", "104": "Perforación en realce",
    "105": "Perforación sostenimiento", "106": "Perforación servicios", "107": "Otros (especifique)", "201": "Reparto de guardia", "202": "Traslado a mina",
    "203": "Traslado a labor", "204": "Inspección de equipo", "205": "Falta o abastecimiento de combustible", "206": "Esperando orden", "207": "Traslado de equipo",
    "208": "Inspección de labor / desate", "209": "Instalación de agua y energía", "210": "Esperando frente de trabajo", "211": "Falta o falla de acero",
    "212": "Recuperación de aceros", "213": "Ventilación deficiente", "214": "Voladura secundaria", "215": "Otros (especifique)", "216": "Falta de agua",
    "217": "Desayuno / almuerzo", "301": "Falla mecánica", "302": "Accesorios", "303": "Mantenimiento programado", "304": "Otros (especifique)",
}


def jackleg() -> tuple[str, Flattening, dict[str, Any]]:
    """Conventional Jackleg form (ejemplo_02). The drilling mesh is a drawing: it is not read."""
    spec = Flattening(corners=[[274, 150], [1133, 154], [1174, 530], [228, 526]], target=[[200, 330], [2700, 330], [2700, 1374], [200, 1374]],
                      strips=[330, 650, 970, 1210, 1450, 1690, 1930, 2170, 2330], y_range=(392, 1047), rows=22, bands=[440, 600, 760, 960])
    activities = _table("activities", "Actividades", (301, 2330), 408, 28.5, 22, [
        {"id": "code", "label": "Código de actividad", "type": "code", "code_set": "jackleg_codes", "x0": 301, "x1": 456, "required": "if_row"},
        {"id": "start", "label": "Hora inicial", **TIME, "x0": 456, "x1": 617, "required": "if_row"},
        {"id": "end", "label": "Hora final", **TIME, "x0": 617, "x1": 768, "required": "if_row"},
        {"id": "labor", "label": "Labor", **TEXT, "x0": 768, "x1": 1104},
        {"id": "length", "label": "Longitud de perforación", **TEXT, "x0": 1104, "x1": 1253},
        {"id": "holes", "label": "N.° de taladros", **INT, "min": 0, "max": 99, "x0": 1253, "x1": 1411},
        {"id": "ore", "label": "Mineral", **TEXT, "x0": 1411, "x1": 1518},
        {"id": "waste", "label": "Desmonte", **TEXT, "x0": 1518, "x1": 1632},
        {"id": "notes", "label": "Observaciones", **TEXT, "x0": 1632, "x1": 2330}])
    steel = _table("steel", "Control de aceros", (1744, 2180), 1091, 28.8, 10, [
        {"id": "equipment", "label": "Código de equipo", **TEXT, "x0": 1744, "x1": 1959},
        {"id": "bits", "label": "N.° de brocas", **INT, "x0": 1959, "x1": 2070},
        {"id": "rods", "label": "N.° de barras", **INT, "x0": 2070, "x1": 2180}])
    tables = [activities, steel]
    definition = {
        "template_id": "reporte-perforacion-jackleg", "version": "jackleg-1.0", "valid_from": "2026-10-30",
        "description": "Formato real de perforación convencional Jackleg, medido sobre la foto ejemplo_02 aplanada (sin escaneo en blanco disponible)",
        "fields": [
            _field("header.date", "Fecha", "date", (603, 216, 1103, 262), "always"),
            _field("header.shift", "Turno", "code", (603, 265, 1103, 326), "always", code_set="shift"),
            _field("header.crew", "Guardia", "code", (2493, 85, 2692, 135), "always"),
            _field("header.equipment", "Código del equipo", "code", (2096, 44, 2382, 96), "always"),
            _field("header.company", "Empresa", "free_text", (2500, 20, 2700, 64)),
            _field("header.operator", "Operador de equipo", "free_text", (2096, 159, 2692, 206), "always"),
            _field("header.initial_state", "Estado inicial del equipo", "free_text", (2132, 106, 2390, 154)),
            _field("header.holes_design", "N.° de taladros de diseño", "numeric", (355, 245, 452, 280), subtype="integer"),
            _field("header.holes_done", "N.° de taladros ejecutados", "numeric", (355, 283, 452, 325), subtype="integer"),
            _field("header.drilling_type", "Tipo de perforación", "free_text", (1147, 250, 2692, 302)),
            _field("closing.location", "Ubicación del equipo", "free_text", (544, 1470, 1566, 1503)),
            _field("closing.final_state", "Estado final del equipo", "free_text", (1926, 1470, 2698, 1506)),
        ],
        "tables": tables,
        "signatures": [_signature("signatures.operator", "Operador de equipo", 198, 640, 1576), _signature("signatures.shift_boss", "Jefe de guardia de mina", 860, 1301, 1576),
                       _signature("signatures.supervisor", "Supervisor de primera línea", 1662, 2118, 1576), _signature("signatures.drilling_blasting", "Perforación y voladura", 2257, 2698, 1576)],
    }
    return "Formato_fisico_ejemplo_02.jpeg", spec, definition


def build(photos: Path, base: dict[str, Any], out_json: Path, out_images: Path, images_ref: str) -> list[Path]:
    """Writes the flat references (out of git) and the template definitions. `base` is template v2: it lends its code sets."""
    import cv2  # noqa: PLC0415

    written: list[Path] = []
    out_json.mkdir(parents=True, exist_ok=True)
    out_images.mkdir(parents=True, exist_ok=True)
    for name, make in (("real_tl", long_hole), ("jackleg", jackleg)):
        photo, spec, definition = make()
        reference = out_images / f"{name}.png"
        cv2.imwrite(str(reference), remove_pen(flatten(photos / photo, spec)))
        sets = dict(base["code_sets"])
        if name == "jackleg":
            sets = {"shift": base["code_sets"]["shift"], "jackleg_codes": {"values": sorted(JACKLEG_LEGEND)}}
        full = {**definition, "reference": {"width": W, "height": H, "dpi": DPI}, "reference_image": f"{images_ref}/{name}.png", "alignment": ALIGNMENT, "photo_cleanup": True,
                "anchors": _corners(definition["tables"]), "code_sets": sets, "source": {"photo": photo}}
        path = out_json / f"{name}.json"
        path.write_text(json.dumps(full, ensure_ascii=False, indent=1) + "\n")
        written += [path, reference]
    return written
