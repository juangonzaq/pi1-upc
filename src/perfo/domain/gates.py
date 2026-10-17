"""Promotion gates G1–G6 for model candidates (pure decision logic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GateResult:
    gate: str
    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateTargets:
    ece_max: float = 0.05
    risk_max: float = 0.01
    latency_p95_max_s: float = 60.0
    field_accuracy_tolerance: float = 0.0
    cer_tolerance: float = 0.0


def g1_integrity(provenance_complete: bool, leakage: list[str], excluded_conflicts: int) -> GateResult:
    ok = provenance_complete and not leakage
    detail = "Procedencia completa y sin fuga del conjunto de prueba" if ok else (
        "Fuga detectada: " + ", ".join(leakage[:5]) if leakage else "Procedencia incompleta"
    )
    return GateResult("G1", "Integridad", ok, detail, {"leakage": leakage, "excluded_conflicts": excluded_conflicts})


def g2_quality(active: dict[str, float], candidate: dict[str, float], t: GateTargets) -> GateResult:
    cer_ok = candidate["cer"] <= active["cer"] + t.cer_tolerance
    acc_ok = candidate["field_accuracy"] + t.field_accuracy_tolerance >= active["field_accuracy"]
    detail = (
        f"CER {active['cer']:.4f} → {candidate['cer']:.4f}; "
        f"exactitud por campo {active['field_accuracy']:.4f} → {candidate['field_accuracy']:.4f}"
    )
    return GateResult("G2", "Calidad", cer_ok and acc_ok, detail, {"active": active, "candidate": candidate})


def g3_calibration(ece: float | None, risk: float | None, coverage: float | None, t: GateTargets) -> GateResult:
    if ece is None or risk is None:
        return GateResult("G3", "Calibración", False, "Sin métricas de calibración", {})
    ok = ece <= t.ece_max and risk <= t.risk_max
    detail = f"ECE {ece:.4f} (meta ≤ {t.ece_max}); riesgo {risk:.4f} (meta ≤ {t.risk_max}) a cobertura {coverage or 0:.3f}"
    return GateResult("G3", "Calibración", ok, detail, {"ece": ece, "risk": risk, "coverage": coverage})


def g4_homologation(active: dict[str, float], candidate: dict[str, float]) -> GateResult:
    ok = candidate["acc_at_1"] >= active["acc_at_1"] and candidate["recall_at_3"] >= active["recall_at_3"]
    detail = (
        f"Exactitud@1 {active['acc_at_1']:.3f} → {candidate['acc_at_1']:.3f}; "
        f"Recall@3 {active['recall_at_3']:.3f} → {candidate['recall_at_3']:.3f}"
    )
    return GateResult("G4", "Homologación", ok, detail, {"active": active, "candidate": candidate})


def g5_operation(latency_p95_s: float, reproducible: bool, artifact_sha256: str | None, t: GateTargets) -> GateResult:
    ok = latency_p95_s <= t.latency_p95_max_s and reproducible and bool(artifact_sha256)
    detail = f"Latencia p95 {latency_p95_s:.2f} s (presupuesto {t.latency_p95_max_s} s); artefacto reproducible: {'sí' if reproducible else 'no'}"
    return GateResult("G5", "Operación", ok, detail, {"latency_p95_s": latency_p95_s, "artifact_sha256": artifact_sha256})


def g6_approval(approver: str | None, approved_at: str | None, version: str, evidence_uri: str | None) -> GateResult:
    ok = bool(approver and approved_at and evidence_uri)
    detail = f"Aprobado por {approver} el {approved_at}" if ok else "Pendiente de aprobación explícita (perfo promote)"
    return GateResult("G6", "Aprobación", ok, detail, {"approver": approver, "at": approved_at, "version": version, "evidence": evidence_uri})


def all_passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)
