"""Confidence routing: auto-accept or human review, with forcing gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Route(StrEnum):
    AUTO = "AUTO"
    REVIEW = "REVIEW"


REASON_TEXT = {
    "unreliable_confidence": "Confianza no fiable",
    "no_threshold": "Sin umbral calibrado para el tipo de campo",
    "low_confidence": "Confianza bajo el umbral",
    "rule_violation": "Regla de negocio violada",
    "homologation_below_threshold": "Homologación bajo su umbral",
    "required_missing": "Campo obligatorio vacío",
    "repeat_mark": "Valor inferido de un trazo de repetición",
    "parse_error": "No se pudo tipar el valor",
}


@dataclass(frozen=True)
class RoutingPolicy:
    thresholds: dict[str, float | None]  # per field type; None => never auto-accept
    repeat_mark_forces_review: bool = True


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RoutingInput:
    field_type: str
    confidence: float | None
    reliable: bool
    rule_violations: tuple[str, ...] = ()
    parse_ok: bool = True
    homologation_ok: bool | None = None  # None when the field is not homologated
    required_missing: bool = False
    repeat_mark: bool = False


def decide_route(inp: RoutingInput, policy: RoutingPolicy) -> RouteDecision:
    reasons: list[str] = []
    if inp.required_missing:
        reasons.append("required_missing")
    if not inp.parse_ok:
        reasons.append("parse_error")
    for rule in inp.rule_violations:
        reasons.append(f"rule_violation:{rule}")
    if inp.homologation_ok is False:
        reasons.append("homologation_below_threshold")
    if inp.repeat_mark and policy.repeat_mark_forces_review:
        reasons.append("repeat_mark")

    threshold = policy.thresholds.get(inp.field_type)
    if not inp.repeat_mark:
        if inp.confidence is None or not inp.reliable:
            reasons.append("unreliable_confidence")
        elif threshold is None:
            reasons.append("no_threshold")
        elif inp.confidence < threshold:
            reasons.append("low_confidence")

    return RouteDecision(Route.REVIEW if reasons else Route.AUTO, tuple(reasons))


def describe(reason: str) -> str:
    base, _, detail = reason.partition(":")
    text = REASON_TEXT.get(base, base)
    return f"{text} ({detail})" if detail else text
