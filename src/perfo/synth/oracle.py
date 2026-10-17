"""Simulated humans: the oracle validator (uses ground truth) and the shift boss approval."""

from __future__ import annotations

import random
from typing import Any

from perfo.application.validation import Decision, ValidationService
from perfo.ml.groundtruth import GroundTruth


def oracle_decisions(svc: ValidationService, doc_id: str, form_id: str, gt: GroundTruth) -> dict[str, Decision]:
    """Touches only the fields routed to review: confirms, chooses a candidate (alias) or corrects with the truth."""
    decisions: dict[str, Decision] = {}
    fields = gt.fields(form_id)
    for fv in svc.pending(doc_id):
        truth = gt.value(form_id, fv.field_id)
        lit = (fields.get(fv.field_id) or {}).get("text")
        if truth is None:
            decisions[fv.field_id] = Decision("clear")
        elif fv.value == truth:
            decisions[fv.field_id] = Decision("confirm")
        elif fv.homologation is not None and truth in [c["code"] for c in fv.homologation.candidates[:3]]:
            decisions[fv.field_id] = Decision("choose", value=truth)
        else:
            decisions[fv.field_id] = Decision("correct", value=truth, text=lit)
    return decisions


def shift_boss_decision(rng: random.Random, observe_rate: float, supervisors: list[str]) -> dict[str, Any]:
    approver = rng.choice(supervisors) if supervisors else "jefe.guardia.sim"
    if rng.random() < observe_rate:
        return {"decision": "OBSERVADO", "approver": approver,
                "comment": rng.choice(["Verificar horómetro final", "Revisar labor de la fila 2", "Confirmar códigos de demora"])}
    return {"decision": "APROBADO", "approver": approver, "comment": "Conforme"}
