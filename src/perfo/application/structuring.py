"""Structuring: typing, repetition marks, homologation, business rules and routing of each field."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from perfo.application.homologation import Homologator
from perfo.domain.entities import HomologationResult
from perfo.domain.fields import REQUIRED_ALWAYS, FieldSpec, FieldType, Template, cell_id
from perfo.domain.parsing import ParseResult, parse_field
from perfo.domain.routing import RouteDecision, RoutingInput, RoutingPolicy, decide_route
from perfo.domain.rules import RuleConfig, evaluate_rules


@dataclass
class FieldInput:
    spec: FieldSpec
    crop_id: str | None
    text: str | None  # recognized text (None for empty cells and marks)
    confidence: float | None
    reliable: bool
    recognition_id: str | None
    mark: str | None = None
    is_empty: bool = False


@dataclass
class StructuredField:
    inp: FieldInput
    parse: ParseResult | None
    value: str | None
    numeric: float | None
    homologation: HomologationResult | None = None
    violations: list[dict[str, Any]] = field(default_factory=list)
    decision: RouteDecision | None = None
    required_missing: bool = False


def effective_code_sets(template: Template, codes_master_items: list[Any] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, cs in template.code_sets.items():
        d = dict(cs)
        if cs.get("master") and codes_master_items is not None:
            d["values"] = [i.code for i in codes_master_items if i.extra.get("tipo") == cs.get("kind")]
        out[name] = d
    return out


def structure(
    template: Template,
    inputs: list[FieldInput],
    code_sets: dict[str, dict],
    homologator: Homologator | None,
    policy: RoutingPolicy,
    rule_cfg: RuleConfig,
    low_resolution: bool = False,
) -> list[StructuredField]:
    by_id = {i.spec.id: i for i in inputs}

    # shift context for 12 h times
    shift = None
    sh = by_id.get("header.shift")
    if sh is not None and sh.text:
        p = parse_field(sh.spec, sh.text, None, code_sets)
        shift = p.value if p.ok else None

    out: dict[str, StructuredField] = {}
    ordered = sorted(inputs, key=lambda i: (i.spec.table or "", i.spec.row or 0, i.spec.id))
    for inp in ordered:
        spec = inp.spec
        if inp.mark == "repeat" and spec.is_cell and spec.row and spec.row > 1:
            prev = out.get(cell_id(spec.table, spec.row - 1, spec.col))  # type: ignore[arg-type]
            if prev is not None and prev.value is not None:
                sf = StructuredField(inp, ParseResult(prev.value, True, None, prev.numeric), prev.value, prev.numeric)
                sf.homologation = prev.homologation
            else:
                sf = StructuredField(inp, ParseResult(None, False, "repeticion_sin_valor_previo"), None, None)
            out[spec.id] = sf
            continue
        if not inp.text:
            sf = StructuredField(inp, None, None, None)
            out[spec.id] = sf
            continue
        p = parse_field(spec, inp.text, shift, code_sets)
        sf = StructuredField(inp, p, p.value, p.numeric)
        if spec.type == FieldType.MASTER_REF and homologator is not None and spec.master_type:
            h = homologator.homologate(spec.master_type, inp.text)
            sf.homologation = h
            sf.value = h.code
        out[spec.id] = sf

    present = {fid for fid, sf in out.items() if sf.inp.text or sf.inp.mark}
    numeric = {fid: sf.numeric for fid, sf in out.items() if sf.numeric is not None and (sf.parse is None or sf.parse.ok)}
    violations = evaluate_rules(template, present, numeric, shift, rule_cfg)

    results: list[StructuredField] = []
    for fid, sf in out.items():
        vs = violations.get(fid, [])
        sf.violations = [{"rule": v.rule_id, "message": v.message, "critical": v.critical} for v in vs]
        is_present = fid in present
        sf.required_missing = any(v.rule_id == "campo_obligatorio_vacio" for v in vs)
        if not is_present and not sf.required_missing:
            continue  # empty optional cell: nothing to route
        parse_ok = sf.parse.ok if sf.parse is not None else False
        rule_ids = tuple(v.rule_id for v in vs if v.rule_id != "campo_obligatorio_vacio")
        if sf.parse is not None and not sf.parse.ok and sf.parse.error:
            rule_ids = (*rule_ids, sf.parse.error)
        hom_ok = None if sf.homologation is None else bool(sf.homologation.accepted)
        sf.decision = decide_route(
            RoutingInput(
                field_type=sf.inp.spec.type.value,
                confidence=sf.inp.confidence,
                reliable=sf.inp.reliable,
                rule_violations=rule_ids,
                parse_ok=parse_ok or not is_present,
                homologation_ok=hom_ok,
                required_missing=sf.required_missing,
                repeat_mark=sf.inp.mark == "repeat",
                low_resolution=low_resolution,
            ),
            policy,
        )
        results.append(sf)
    # header fields first, then tables in row order
    order = {f.id: i for i, f in enumerate(template.fields)}
    results.sort(key=lambda s: order.get(s.inp.spec.id, 0))
    return results


def required_specs(template: Template) -> list[FieldSpec]:
    return [f for f in template.prioritized() if f.required == REQUIRED_ALWAYS]
