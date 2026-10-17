"""Record-level business rules. Each violation is attached to the fields it involves."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from perfo.domain.fields import REQUIRED_ALWAYS, REQUIRED_IF_ROW, FieldSpec, Template, cell_id

DEVIATION_LIMIT_PCT = 2.0


@dataclass(frozen=True)
class Violation:
    rule_id: str
    message: str
    critical: bool = False  # critical violations block confirmation (HU07)


@dataclass
class RuleConfig:
    deviation_limit_pct: float = DEVIATION_LIMIT_PCT
    deviation_pct_tolerance: float = 0.15
    max_shift_hours: float = 13.0
    max_angle_diff_deg: float = 10.0


def _f(values: dict[str, float | None], key: str) -> float | None:
    return values.get(key)


def evaluate_rules(
    template: Template,
    present: set[str],
    numeric: dict[str, float | None],
    shift: str | None,
    config: RuleConfig | None = None,
) -> dict[str, list[Violation]]:
    """Evaluates cross-field rules.

    present: field ids with content (recognized or inferred).
    numeric: numeric view of parsed values (numbers; minutes for times); None when unparsable.
    """
    cfg = config or RuleConfig()
    out: dict[str, list[Violation]] = defaultdict(list)

    def add(fields: list[str], rule: str, msg: str, critical: bool = False) -> None:
        v = Violation(rule, msg, critical)
        for f in fields:
            out[f].append(v)

    # Required fields (header) and required-if-row (table cells)
    rows_with_content: dict[tuple[str, int], bool] = defaultdict(bool)
    for f in template.prioritized():
        if f.is_cell and f.id in present:
            rows_with_content[(f.table, f.row)] = True  # type: ignore[index]
    for f in template.prioritized():
        if f.required == REQUIRED_ALWAYS and f.id not in present:
            add([f.id], "campo_obligatorio_vacio", f"{f.label}: campo obligatorio vacío", critical=True)
        if f.required == REQUIRED_IF_ROW and f.is_cell and f.id not in present:
            if rows_with_content[(f.table, f.row)]:  # type: ignore[index]
                add([f.id], "campo_obligatorio_vacio", f"{f.label} (fila {f.row}): obligatorio en filas con datos", critical=True)

    # Hour meters: final >= initial, plausible delta, percussion <= engine
    pairs = [("header.engine_hm_start", "header.engine_hm_end"), ("header.percussion_hm_start", "header.percussion_hm_end")]
    deltas: dict[str, float] = {}
    for a, b in pairs:
        va, vb = _f(numeric, a), _f(numeric, b)
        if va is None or vb is None:
            continue
        if vb < va:
            add([a, b], "horometro_final_menor", "El horómetro final es menor que el inicial")
        elif vb - va > cfg.max_shift_hours:
            add([a, b], "horometro_delta_excesivo", f"Diferencia de horómetro mayor a {cfg.max_shift_hours} h en una guardia")
        else:
            deltas[a] = vb - va
    if "header.engine_hm_start" in deltas and "header.percussion_hm_start" in deltas:
        if deltas["header.percussion_hm_start"] > deltas["header.engine_hm_start"] + 1e-6:
            add(
                ["header.percussion_hm_start", "header.percussion_hm_end"],
                "percusion_mayor_motor",
                "Las horas de percusión superan a las horas de motor",
            )

    # Table rules
    for table in {f.table for f in template.prioritized() if f.is_cell}:
        rows = sorted({f.row for f in template.prioritized() if f.table == table})
        for r in rows:
            def c(col: str) -> str:
                return cell_id(table, r, col)  # type: ignore[arg-type]

            start, end = _f(numeric, c("start")), _f(numeric, c("end"))
            if start is not None and end is not None:
                dur = (end - start) % (24 * 60)
                if dur > cfg.max_shift_hours * 60:
                    add([c("start"), c("end")], "hora_final_inconsistente", "La hora final no es posterior a la inicial dentro de la guardia")
            if table != "activities":
                continue
            length, dev_m, dev_pct = _f(numeric, c("length_m")), _f(numeric, c("deviation_m")), _f(numeric, c("deviation_pct"))
            if length and dev_m is not None:
                expected = dev_m / length * 100.0
                if dev_pct is not None and abs(expected - dev_pct) > cfg.deviation_pct_tolerance:
                    add(
                        [c("deviation_m"), c("deviation_pct"), c("length_m")],
                        "desviacion_pct_inconsistente",
                        f"% desviación ({dev_pct}) ≠ desviación/longitud×100 ({expected:.2f})",
                    )
                pct = dev_pct if dev_pct is not None else expected
                if pct > cfg.deviation_limit_pct:
                    add(
                        [c("deviation_m"), c("deviation_pct")],
                        "desviacion_mayor_2pct",
                        f"Desviación de {pct:.2f} % mayor que {cfg.deviation_limit_pct} %: debe corregirse",
                    )
            planned, executed = _f(numeric, c("angle_planned")), _f(numeric, c("angle_executed"))
            if planned is not None and executed is not None and abs(planned - executed) > cfg.max_angle_diff_deg:
                add([c("angle_planned"), c("angle_executed")], "angulo_inconsistente", "Ángulo ejecutado muy distinto del programado")

    return dict(out)


def critical_pending(spec: FieldSpec, violations: list[Violation]) -> bool:
    return any(v.critical for v in violations)
