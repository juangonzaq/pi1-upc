"""Plausible and consistent content for one synthetic form, with literal (handwritten) text and canonical value."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from perfo.domain.parsing import fmt_minutes, format_decimal
from perfo.synth.layout import ACTIVITY_CODES, DELAY_CODES, FAILURE_CODES
from perfo.synth.masters import equipment_variant, labor_variant, person_variant


@dataclass
class OperatorProfile:
    code: str
    style_id: int
    time_style: str  # 24h | 12h | 12h_suffix
    decimal_comma: bool
    date_style: int
    labor_upper: bool


@dataclass
class FormContent:
    form_id: str
    batch: str
    date: str
    shift: str
    crew: str
    equipment: str
    operator: str
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra_text: dict[str, str] = field(default_factory=dict)  # non-prioritized fields (observations, ...)
    diagonal: list[dict[str, Any]] = field(default_factory=list)  # labor written diagonally across rows
    repeat_runs: list[dict[str, Any]] = field(default_factory=list)  # vertical repetition strokes
    signatures: dict[str, bool] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)

    def put(self, fid: str, value: str | None, text: str | None, mark: str | None = None) -> None:
        self.fields[fid] = {"value": value, "text": text, "mark": mark}


def crew_for(day_index: int, shift: str) -> str:
    return "ABC"[(day_index * 2 + (0 if shift == "D" else 1)) % 3]


def write_date(d: date, style: int) -> str:
    if style == 0:
        return d.strftime("%d/%m/%Y")
    if style == 1:
        return d.strftime("%d/%m/%y")
    if style == 2:
        return f"{d.day}/{d.month}/{d.strftime('%y')}"
    return d.strftime("%d-%m-%y")


def write_time(minutes: int, op: OperatorProfile, rng: random.Random) -> str:
    minutes %= 24 * 60
    h, m = divmod(minutes, 60)
    style = op.time_style if rng.random() < 0.85 else rng.choice(["24h", "12h", "12h_suffix"])
    sep = ":" if rng.random() < 0.85 else "."
    if style == "24h":
        return f"{h:02d}{sep}{m:02d}" if rng.random() < 0.6 else f"{h}{sep}{m:02d}"
    h12 = h % 12 or 12
    text = f"{h12}{sep}{m:02d}"
    if style == "12h_suffix":
        text += rng.choice([" pm", "pm", " p.m.", "p"]) if h >= 12 else rng.choice([" am", "am", " a.m.", "a"])
    return text


def write_number(v: float, decimals: int, op: OperatorProfile) -> str:
    s = f"{v:.{decimals}f}" if decimals else str(int(round(v)))
    return s.replace(".", ",") if op.decimal_comma and decimals else s


def generate_content(
    form_id: str,
    batch: str,
    rng: random.Random,
    d: date,
    day_index: int,
    shift: str,
    equipment: dict[str, Any],
    operator: dict[str, Any],
    op: OperatorProfile,
    masters: dict[str, list[dict[str, Any]]],
    cfg: dict[str, Any],
) -> FormContent:
    crew = crew_for(day_index, shift)
    fc = FormContent(form_id=form_id, batch=batch, date=d.isoformat(), shift=shift, crew=crew, equipment=equipment["codigo"], operator=operator["codigo"])
    labores = masters["labores"]
    stopes = [l for l in labores if l["tipo"] == "TAJO"]
    stope = rng.choice(stopes)

    # ---------------- header
    fc.put("header.date", d.isoformat(), write_date(d, op.date_style))
    fc.put("header.shift", shift, shift if rng.random() < 0.8 else ("DIA" if shift == "D" else "NOCHE"))
    fc.put("header.crew", crew, crew)
    fc.put("header.equipment", equipment["codigo"], equipment_variant(equipment, rng))
    fc.put("header.stope", stope["codigo"], labor_variant(stope["descripcion"], rng, op.labor_upper))
    fc.put("header.operator", operator["codigo"], person_variant(operator, rng))
    eng0 = round(rng.uniform(4500, 12000), 1)
    eng1 = round(eng0 + rng.uniform(6.0, 11.5), 1)
    per0 = round(rng.uniform(2000, 8000), 1)
    per1 = round(per0 + rng.uniform(2.5, min(7.0, eng1 - eng0)), 1)
    if rng.random() < 0.03:  # hour-meter inconsistency written on the form
        eng0, eng1 = eng1, eng0
        fc.flags["hour_meter_violation"] = True
    for fid, v in (("header.engine_hm_start", eng0), ("header.engine_hm_end", eng1), ("header.percussion_hm_start", per0), ("header.percussion_hm_end", per1)):
        fc.put(fid, format_decimal(v), write_number(v, 1, op))
    fc.extra_text["header.initial_state"] = rng.choice(["OPERATIVO", "OPERATIVO", "OPERATIVO", "OBSERVADO"])

    shift_start = 7 * 60 if shift == "D" else 19 * 60
    t = shift_start + rng.choice([0, 5, 10, 15, 20, 30])

    # ---------------- activities
    n_rows = rng.randint(2, int(cfg["max_activity_rows"]))
    prev_labor: dict[str, Any] | None = None
    prev_vals: dict[str, str] = {}
    labor_for_row: list[dict[str, Any]] = []
    for r in range(1, n_rows + 1):
        if prev_labor is not None and rng.random() < 0.55:
            labor = prev_labor
        else:
            labor = stope if rng.random() < 0.6 else rng.choice(labores)
        labor_for_row.append(labor)
        prev_labor = labor

    diag_start = None
    if n_rows >= 3 and rng.random() < cfg.get("diagonal_rate", 0.12):
        cand = [r for r in range(1, n_rows - 1)]
        diag_start = rng.choice(cand)
        span = min(3, n_rows - diag_start + 1)
        for k in range(span):
            labor_for_row[diag_start - 1 + k] = labor_for_row[diag_start - 1]
        fc.diagonal.append({"table": "activities", "col": "labor", "row": diag_start, "span": span,
                            "text": labor_variant(labor_for_row[diag_start - 1]["descripcion"], rng, op.labor_upper)})

    diag_rows = set()
    for dg in fc.diagonal:
        diag_rows.update(range(dg["row"], dg["row"] + dg["span"]))

    codes: list[str] = []
    for r in range(1, n_rows + 1):
        if rng.random() < 0.02:
            codes.append(rng.choice(["108", "110", "170", "011", "201"]))
            fc.flags.setdefault("invalid_codes", []).append(f"activities.r{r:02d}.code")
        else:
            codes.append(rng.choices(list(ACTIVITY_CODES), weights=[40, 10, 10, 12, 12, 8, 8])[0])
    durations = [rng.randint(25, 140) if c in ("101", "102", "103") else rng.randint(10, 60) for c in codes]
    gaps = [rng.choice([0, 0, 5, 10]) for _ in codes]
    budget = 640 - (t - shift_start)  # all times stay inside the shift window
    total = sum(durations) + sum(gaps)
    if total > budget:
        factor = budget / total
        durations = [max(5, int(d * factor)) for d in durations]
        gaps = [int(g * factor) for g in gaps]

    for r in range(1, n_rows + 1):
        p = f"activities.r{r:02d}."
        code = codes[r - 1]
        drilling = code in ("101", "102", "103")
        start, end = t, t + durations[r - 1]
        t = end + gaps[r - 1]
        fc.put(p + "activity_no", str(r), str(r))
        fc.put(p + "code", code, code)
        fc.put(p + "start", fmt_minutes(start), write_time(start, op, rng))
        fc.put(p + "end", fmt_minutes(end), write_time(end, op, rng))

        labor = labor_for_row[r - 1]
        if r in diag_rows:
            fc.put(p + "labor", labor["codigo"], None, "diagonal")
        elif r > 1 and labor_for_row[r - 2] is labor and (r - 1) not in diag_rows and rng.random() < cfg.get("repeat_rate", 0.35):
            fc.put(p + "labor", labor["codigo"], None, "repeat")
        else:
            fc.put(p + "labor", labor["codigo"], labor_variant(labor["descripcion"], rng, op.labor_upper))

        if drilling:
            row_no = int(prev_vals.get("row_no", rng.randint(1, 25)))
            if rng.random() < 0.6:
                row_no = row_no + 1 if "row_no" in prev_vals else row_no
            holes = rng.randint(1, 12)
            length = round(rng.uniform(3.0, 15.0), 1)
            bars = max(1, math.ceil(length / 1.52))
            planned = float(rng.choice([45, 60, 70, 75, 80, 90, 100, 105, 110, 120, 135]))
            executed = planned + rng.choice([0, 0, 0, 0.5, 1, -1, 2, -2])
            if rng.random() < 0.12:
                dev = round(length * rng.uniform(0.021, 0.05), 2)
                fc.flags.setdefault("deviation_violations", []).append(p + "deviation_pct")
            else:
                dev = round(length * rng.uniform(0.0, 0.0195), 2)
            pct = round(dev / length * 100, 1)
            if rng.random() < 0.04:
                pct = round(pct + rng.choice([-1, 1]) * rng.uniform(0.5, 1.5), 1)
                pct = abs(pct)
                fc.flags.setdefault("pct_inconsistent", []).append(p + "deviation_pct")
            vals = {"row_no": str(row_no), "holes": str(holes), "bars": str(bars)}
            for col, v in vals.items():
                if col == "row_no" and prev_vals.get(col) == v and rng.random() < 0.4:
                    fc.put(p + col, v, None, "repeat")
                else:
                    fc.put(p + col, v, v)
            fc.put(p + "length_m", format_decimal(length), write_number(length, 1, op))
            ap = format_decimal(planned)
            if prev_vals.get("angle_planned") == ap and rng.random() < 0.4:
                fc.put(p + "angle_planned", ap, None, "repeat")
            else:
                fc.put(p + "angle_planned", ap, ap + ("°" if rng.random() < 0.2 else ""))
            fc.put(p + "angle_executed", format_decimal(executed), write_number(executed, 1 if executed % 1 else 0, op))
            fc.put(p + "deviation_m", format_decimal(dev), write_number(dev, 2, op))
            fc.put(p + "deviation_pct", format_decimal(pct), write_number(pct, 1, op))
            prev_vals = {"row_no": str(row_no), "angle_planned": ap}
            if rng.random() < 0.15:
                fc.extra_text[p + "reamed_hole"] = "SI"
        else:
            prev_vals = {}
        if rng.random() < 0.15:
            fc.extra_text[p + "notes"] = rng.choice(["SIN NOVEDAD", "OK", "AGUA EN LABOR", "REPASO", "ESPERA SCOOP"])

    # ---------------- delays
    n_delays = rng.randint(0, int(cfg["max_delay_rows"]))
    for r in range(1, n_delays + 1):
        p = f"delays.r{r:02d}."
        code = rng.choice(list(DELAY_CODES)) if rng.random() > 0.02 else rng.choice(["218", "220", "120"])
        start = shift_start + rng.randint(30, 600)
        end = start + rng.randint(10, 60)
        labor = stope if rng.random() < 0.7 else rng.choice(labores)
        fc.put(p + "labor", labor["codigo"], labor_variant(labor["descripcion"], rng, op.labor_upper))
        fc.put(p + "code", code, code)
        fc.put(p + "start", fmt_minutes(start), write_time(start, op, rng))
        fc.put(p + "end", fmt_minutes(end), write_time(end, op, rng))

    # ---------------- failures
    n_fail = rng.randint(0, int(cfg["max_failure_rows"])) if rng.random() < 0.5 else 0
    for r in range(1, n_fail + 1):
        p = f"failures.r{r:02d}."
        code = rng.choice(list(FAILURE_CODES))
        start = shift_start + rng.randint(60, 480)
        dur = rng.randint(20, 160)
        fc.put(p + "code", code, code)
        fc.put(p + "start", fmt_minutes(start), write_time(start, op, rng))
        fc.put(p + "end", fmt_minutes(start + dur), write_time(start + dur, op, rng))
        fc.extra_text[p + "total_hours"] = write_number(round(dur / 60, 1), 1, op)
        fc.extra_text[p + "labor"] = labor_variant(stope["descripcion"], rng, op.labor_upper)

    # ---------------- closing
    if rng.random() < 0.75:
        loc = stope if rng.random() < 0.6 else rng.choice(labores)
        fc.put("closing.location", loc["codigo"], labor_variant(loc["descripcion"], rng, op.labor_upper))
    fc.extra_text["closing.final_state"] = rng.choice(["OPERATIVO", "OPERATIVO", "INOPERATIVO"])
    for fid, lo, hi, p_fill in (("closing.gallons", 10, 80, 0.9), ("closing.bits", 0, 4, 0.85), ("closing.rods", 0, 3, 0.85), ("closing.tube", 0, 2, 0.7), ("closing.shank", 0, 2, 0.7)):
        if rng.random() < p_fill:
            v = str(rng.randint(lo, hi))
            fc.put(fid, v, v)
    if rng.random() < 0.3:
        fc.extra_text["closing.general_notes"] = rng.choice(["SIN NOVEDAD", "EQUIPO EN BUEN ESTADO", "CAMBIO DE BROCA"])

    for sig in ("operator", "shift_boss", "maintenance", "drilling_blasting"):
        fc.signatures[f"signatures.{sig}"] = rng.random() < 0.93
    return fc
