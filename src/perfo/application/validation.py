"""Human validation (HU07), immutable storage with traceability (HU09) and approval (HU10/HU11)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from perfo.application.homologation import Homologator
from perfo.application.structuring import effective_code_sets
from perfo.domain.entities import Approval, AuditEvent, Correction, Document, FieldValue, ValidatedRecord, ValidatedValue, new_id, now
from perfo.domain.fields import FieldType, Template
from perfo.domain.parsing import parse_field
from perfo.domain.routing import Route
from perfo.domain.states import DocumentState, transition
from perfo.ports.masters import MasterDataPort
from perfo.ports.repository import RepositoryPort
from perfo.ports.telemetry import TelemetryPort


# rule findings that do not block an explicit validator decision (the value is well formed)
NON_BLOCKING = {"codigo_no_valido", "fuera_de_rango", "hora_fuera_de_turno"}


class ValidationBlocked(Exception):
    def __init__(self, message: str, fields: list[str]):
        super().__init__(message)
        self.fields = fields


@dataclass
class Decision:
    """Validator action on a field: confirm the proposal, correct it, or choose a homologation candidate."""

    action: str  # confirm | correct | choose | clear
    value: str | None = None  # canonical value (code for master_ref)
    text: str | None = None  # literal transcription, when the validator types what is written


@dataclass
class ValidationResult:
    record: ValidatedRecord
    corrections: list[Correction]
    touched: int
    corrected: int
    aliases: int
    stats: dict[str, Any] = field(default_factory=dict)


class ValidationService:
    def __init__(self, repo: RepositoryPort, masters: MasterDataPort, homologator: Homologator, template: Template, telemetry: TelemetryPort,
                 template_for: Callable[[str | None], Template] | None = None):
        self.repo = repo
        self.masters = masters
        self.homologator = homologator
        self.template = template
        self.template_for = template_for or (lambda _version: template)
        self.telemetry = telemetry
        self._warnings: list[str] = []

    # ------------------------------------------------------------------ queries
    def pending(self, doc_id: str) -> list[FieldValue]:
        return [v for v in self.repo.get_field_values(doc_id) if v.route == Route.REVIEW.value]

    def tray(self, **filters: str) -> list[dict[str, Any]]:
        """HU10: confirmed and not approved documents, with filters by date, crew and equipment."""
        out = []
        for d in self.repo.list_documents(states=[DocumentState.VALIDADO.value]):
            date_, crew, equip = (d.business_key or "||").split("|")
            if filters.get("fecha") and filters["fecha"] != date_:
                continue
            if filters.get("guardia") and filters["guardia"] != crew:
                continue
            if filters.get("equipo") and filters["equipo"] != equip:
                continue
            recs = self.repo.get_records(d.id)
            last = recs[-1] if recs else None
            out.append({"document_id": d.id, "fecha": date_, "guardia": crew, "equipo": equip,
                        "operador": next((v.value for v in last.values if v.field_id == "header.operator"), None) if last else None,
                        "version": last.version if last else None, "corregidos": last.stats.get("corrected", 0) if last else 0})
        return out

    # ------------------------------------------------------------------ helpers
    def _move(self, doc: Document, target: DocumentState, cause: str | None, actor: str) -> None:
        prev = doc.state
        doc.state = transition(doc.state, target)
        doc.state_cause = cause
        self.repo.update_document(doc)
        self.repo.record_state_change(doc.id, prev.value, target.value, cause, actor)

    def _canonical(self, template: Template, fv: FieldValue, dec: Decision, shift: str | None, code_sets: dict) -> str | None:
        spec = template.field(fv.field_id)
        if spec.type == FieldType.MASTER_REF:
            code = dec.value
            if code is None and dec.text:
                code = self.homologator.homologate(spec.master_type, dec.text).code  # type: ignore[arg-type]
            if code is None:
                return None
            valid = {i.code for i in self.masters.get_master(spec.master_type).items}  # type: ignore[arg-type]
            if code not in valid:
                raise ValidationBlocked(f"{fv.field_id}: el código {code} no existe en el maestro vigente", [fv.field_id])
            return code
        raw = dec.value if dec.value is not None else dec.text
        if raw is None:
            return None
        p = parse_field(spec, raw, shift, code_sets)
        if not p.ok:
            if p.value is not None and p.error in NON_BLOCKING:
                self._warnings.append(f"{fv.field_id}: {p.error}")
                return p.value
            if p.error == "hora_ambigua":
                raise ValidationBlocked(f"Indique el turno (D o N) para validar la hora {raw}, o escríbala con AM o PM.", ["header.shift", fv.field_id])
            raise ValidationBlocked(f"{fv.field_id}: '{raw}' no es válido ({p.error})", [fv.field_id])
        return p.value

    # ------------------------------------------------------------------ main command
    def confirm(self, doc_id: str, decisions: dict[str, Decision], user: str, accept_rest: bool = False) -> ValidationResult:
        doc = self.repo.get_document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        if doc.state != DocumentState.EN_REVISION:
            raise ValidationBlocked(f"El documento está en estado {doc.state.value}; solo se valida en EN_REVISION", [])
        self._warnings = []
        values = self.repo.get_field_values(doc_id)
        if not values:
            raise ValidationBlocked("El documento no tiene campos reconocidos (¿alineación fallida?); requiere reproceso o recaptura", [])
        unknown = set(decisions) - {v.field_id for v in values}
        if unknown:
            raise ValidationBlocked(f"Campos inexistentes en el documento: {', '.join(sorted(unknown))}", sorted(unknown))
        prev_records = self.repo.get_records(doc_id)
        carried = {v.field_id: v for v in prev_records[-1].values} if prev_records else {}
        review = [v for v in values if v.route == Route.REVIEW.value]
        missing = [v.field_id for v in review if v.field_id not in decisions and v.field_id not in carried]
        if missing and not accept_rest:
            raise ValidationBlocked(f"{len(missing)} campos requieren acción del validador", missing)

        codes = self.masters.get_master("codigos").items
        template = self.template_for(doc.template_version)
        code_sets = effective_code_sets(template, codes)
        shift_fv = next((v for v in values if v.field_id == "header.shift"), None)
        shift = decisions["header.shift"].value if "header.shift" in decisions and decisions["header.shift"].value else (shift_fv.value if shift_fv else None)

        validated: list[ValidatedValue] = []
        corrections: list[Correction] = []
        blocked: list[str] = []
        aliases = 0
        model_version = None
        recs = {r.id: r for r in self.repo.get_recognitions([v.crop_id for v in values if v.crop_id])}
        for fv in values:
            rec = recs.get(fv.recognition_id) if fv.recognition_id else None
            model_version = model_version or (rec.model_version if rec else None)
            dec = decisions.get(fv.field_id)
            if dec is None and fv.field_id in carried:
                prev_v = carried[fv.field_id]
                validated.append(ValidatedValue(fv.field_id, prev_v.value, prev_v.source, fv.id))
                continue
            if fv.route == Route.AUTO.value and dec is None:
                validated.append(ValidatedValue(fv.field_id, fv.value, "auto", fv.id))
                corrections.append(self._correction(doc_id, fv, fv.value, None, rec, user, "confirmed_auto"))
                continue
            dec = dec or Decision("confirm")
            if dec.action == "confirm":
                final = fv.value
                critical = [v for v in fv.violations if v.get("critical")] or (fv.value is None)
                if critical and final is None:
                    blocked.append(fv.field_id)
                    continue
                validated.append(ValidatedValue(fv.field_id, final, "confirmed", fv.id))
                corrections.append(self._correction(doc_id, fv, final, dec.text, rec, user, "confirmed_review"))
                continue
            if dec.action == "clear":
                spec = template.field(fv.field_id)
                if spec.required == "always":
                    blocked.append(fv.field_id)
                    continue
                validated.append(ValidatedValue(fv.field_id, None, "corrected", fv.id))
                corrections.append(self._correction(doc_id, fv, None, "", rec, user, "correction"))
                continue
            final = self._canonical(template, fv, dec, shift, code_sets)
            if final is None:
                blocked.append(fv.field_id)
                continue
            source = "candidate" if dec.action == "choose" else "corrected"
            kind = "correction" if final != fv.value or (dec.text and fv.text and dec.text != fv.text) else "confirmed_review"
            validated.append(ValidatedValue(fv.field_id, final, source if kind == "correction" else "confirmed", fv.id))
            corrections.append(self._correction(doc_id, fv, final, dec.text, rec, user, kind))
            spec = template.field(fv.field_id)
            if spec.type == FieldType.MASTER_REF and fv.text and fv.homologation and (not fv.homologation.accepted or fv.homologation.code != final):
                self.homologator.learn_alias(spec.master_type, fv.text, final, user)  # type: ignore[arg-type]
                aliases += 1
        if blocked:
            raise ValidationBlocked(f"Errores críticos pendientes (tipo de dato u obligatoriedad) en {len(blocked)} campos", blocked)

        prev = prev_records
        payload = {v.field_id: v.value for v in validated}
        sha = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        n_corr = sum(1 for c in corrections if c.kind == "correction")
        touched = sum(1 for c in corrections if c.kind in ("correction", "confirmed_review"))
        stats = {"fields": len(validated), "auto_accepted": sum(1 for v in validated if v.source == "auto"), "reviewed": touched,
                 "corrected": n_corr, "aliases_learned": aliases, "model_version": model_version, "run_id": values[0].run_id,
                 "warnings": list(self._warnings)}
        record = ValidatedRecord(new_id(), doc_id, (prev[-1].version + 1) if prev else 1, user, now(), validated, sha,
                                 prev[-1].id if prev else None, stats)
        with self.repo.transaction():
            self.repo.add_validated_record(record)
            self.repo.add_corrections(corrections)
            self._move(doc, DocumentState.VALIDADO, f"Versión {record.version} confirmada por {user}", user)
            self.repo.add_audit(AuditEvent("record_validated", "document", doc_id, user, after={"record": record.id, "version": record.version, **stats}))
        self.telemetry.event("registro_validado", document_id=doc_id, **stats)
        return ValidationResult(record, corrections, touched, n_corr, aliases, stats)

    def _correction(self, doc_id: str, fv: FieldValue, final: str | None, text: str | None, rec: Any, user: str, kind: str) -> Correction:
        return Correction(
            id=new_id(), document_id=doc_id, field_value_id=fv.id, crop_id=fv.crop_id, field_id=fv.field_id, field_type=fv.field_type,
            recognized_text=fv.text, recognized_value=fv.value, validated_value=final, validated_text=text,
            confidence=fv.confidence, model_version=rec.model_version if rec else None, user=user, created_at=now(), kind=kind,
        )

    # ------------------------------------------------------------------ approval (HU11)
    def approve(self, doc_id: str, decision: str, approver: str, comment: str = "") -> Approval:
        doc = self.repo.get_document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        if doc.state != DocumentState.VALIDADO:
            raise ValidationBlocked(f"Solo se aprueban registros VALIDADO (estado actual {doc.state.value})", [])
        decision = decision.upper()
        if decision not in ("APROBADO", "OBSERVADO"):
            raise ValueError("La decisión debe ser APROBADO u OBSERVADO")
        if decision == "OBSERVADO" and not comment:
            raise ValidationBlocked("Una observación requiere comentario", [])
        record = self.repo.get_records(doc_id)[-1]
        ap = Approval(new_id(), record.id, doc_id, decision, comment, approver, now())
        with self.repo.transaction():
            self.repo.add_approval(ap)
            if decision == "APROBADO":
                self._move(doc, DocumentState.APROBADO, f"Aprobado por {approver}", approver)
            else:
                self._move(doc, DocumentState.EN_REVISION, f"Observado por {approver}: {comment}", approver)
            self.repo.add_audit(AuditEvent("record_" + decision.lower(), "document", doc_id, approver, after={"record": record.id, "comment": comment}))
        return ap

    # ------------------------------------------------------------------ traceability (HU09)
    def trace(self, doc_id: str, field_id: str) -> dict[str, Any]:
        doc = self.repo.get_document(doc_id)
        records = self.repo.get_records(doc_id)
        vv = next((v for v in reversed(records[-1].values) if v.field_id == field_id), None) if records else None
        fv = self.repo.get_field_value(vv.field_value_id) if vv and vv.field_value_id else None
        crop = self.repo.get_crop(fv.crop_id) if fv and fv.crop_id else None
        rec = None
        if fv and fv.recognition_id:
            rec = next((r for r in self.repo.get_recognitions([fv.crop_id]) if r.id == fv.recognition_id), None)  # type: ignore[list-item]
        return {
            "documento": {"id": doc.id, "sha256": doc.sha256, "original": doc.original_key, "preprocesado": doc.preprocessed_key,
                          "plantilla": doc.template_version, "transformacion": {k: v for k, v in (doc.transform or {}).items() if k != "homography"}} if doc else None,
            "registro": {"id": records[-1].id, "version": records[-1].version, "validado_por": records[-1].validated_by,
                         "fecha": records[-1].validated_at.isoformat(), "sha256": records[-1].sha256} if records else None,
            "valor_validado": {"valor": vv.value, "origen": vv.source} if vv else None,
            "valor_propuesto": {"id": fv.id, "corrida": fv.run_id, "texto": fv.text, "valor": fv.value, "ruta": fv.route, "motivos": fv.reasons,
                                "confianza": fv.confidence, "homologacion": fv.homologation.__dict__ if fv.homologation else None} if fv else None,
            "reconocimiento": {"id": rec.id, "modelo": rec.model_version, "texto": rec.text, "score": rec.score, "score_sin_calibrar": rec.raw_score,
                               "perfil_calibracion": rec.calibration_id} if rec else None,
            "recorte": {"id": crop.id, "objeto": crop.object_key, "sha256": crop.sha256, "caja_px": list(crop.box_px), "marca": crop.mark} if crop else None,
        }
