"""Document processing use case: preprocess → segment → recognize → structure/homologate → route."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import cv2

from perfo.application.homologation import Homologator
from perfo.application.preprocess import preprocess
from perfo.application.segment import segment
from perfo.application.structuring import FieldInput, StructuredField, effective_code_sets, structure
from perfo.domain.entities import AuditEvent, Crop, Document, FieldValue, Page, Recognition, new_id
from perfo.domain.fields import Template
from perfo.domain.routing import Route, RoutingPolicy
from perfo.domain.rules import RuleConfig
from perfo.domain.states import DocumentState, transition
from perfo.ports.masters import MasterDataPort
from perfo.ports.recognition import RecognitionOutput, RecognitionPort, RecognizerUnavailable
from perfo.ports.repository import RepositoryPort
from perfo.ports.storage import ObjectStoragePort
from perfo.ports.telemetry import TelemetryPort


@dataclass
class ProcessOutcome:
    document_id: str
    state: str
    cause: str | None
    run_id: str | None = None
    fields: int = 0
    review: int = 0
    seconds: float = 0.0
    stages: dict[str, float] = field(default_factory=dict)


class DocumentProcessor:
    def __init__(
        self,
        repo: RepositoryPort,
        storage: ObjectStoragePort,
        telemetry: TelemetryPort,
        template: Template,
        reference_path: str,
        cfg: Any,
        recognizer: Callable[[], RecognitionPort] | None,
        homologator: Homologator | None,
        masters: MasterDataPort | None,
        routing_policy: Callable[[], RoutingPolicy],
        actor: str = "sistema",
    ):
        self.repo = repo
        self.storage = storage
        self.telemetry = telemetry
        self.template = template
        self.reference_path = reference_path
        self.cfg = cfg
        self.recognizer = recognizer
        self.homologator = homologator
        self.masters = masters
        self.routing_policy = routing_policy
        self.actor = actor

    # ------------------------------------------------------------------ helpers
    def _move(self, doc: Document, target: DocumentState, cause: str | None) -> None:
        prev = doc.state
        doc.state = transition(doc.state, target)
        doc.state_cause = cause
        self.repo.update_document(doc)
        self.repo.record_state_change(doc.id, prev.value, target.value, cause, self.actor)

    def _recognize_with_retry(self, items: list[tuple[bytes, str]]) -> list[RecognitionOutput]:
        assert self.recognizer is not None
        retries = int(self.cfg.get_path("recognition.max_retries", 2))
        backoff = float(self.cfg.get_path("recognition.retry_backoff_s", 0.2))
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self.recognizer().recognize_batch(items)
            except RecognizerUnavailable as exc:
                last = exc
                self.telemetry.event("reconocedor_no_disponible", attempt=attempt + 1, error=str(exc))
                time.sleep(backoff * (attempt + 1))
        raise RecognizerUnavailable(f"Reconocedor no disponible tras {retries + 1} intentos: {last}")

    # ------------------------------------------------------------------ main use case
    def process(self, doc_id: str, until: str | None = None) -> ProcessOutcome:
        doc = self.repo.get_document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        if doc.state == DocumentState.CARGADO:
            self._move(doc, DocumentState.EN_COLA, None)
        if doc.state != DocumentState.EN_COLA:
            return ProcessOutcome(doc.id, doc.state.value, f"El documento no está en cola (estado {doc.state.value})")
        t_start = time.perf_counter()
        stages: dict[str, float] = {}

        # 1. quality control + preprocessing
        t0 = time.perf_counter()
        original = self.storage.get(doc.original_key)
        pre = preprocess(original, self.template, self.reference_path, self.cfg.get_path("preprocessing", {}))
        stages["preprocess"] = time.perf_counter() - t0
        doc.template_version = self.template.version
        if not pre.ok:
            doc.transform = pre.transform
            self._move(doc, DocumentState.ERROR, pre.cause)
            self.telemetry.event("procesamiento_bloqueado", document_id=doc.id, cause=pre.cause)
            return ProcessOutcome(doc.id, doc.state.value, pre.cause, seconds=time.perf_counter() - t_start, stages=stages)
        ok, buf = cv2.imencode(".jpg", pre.image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        pre_bytes = buf.tobytes()
        pre_key = f"preprocessed/{doc.id}/{hashlib.sha256(pre_bytes).hexdigest()[:16]}.jpg"
        self.storage.put(pre_key, pre_bytes, {"document_id": doc.id, "template_version": self.template.version, "transform": pre.transform})
        doc.preprocessed_key = pre_key
        doc.transform = pre.transform
        page = Page(new_id(), doc.id, 1, pre_key, self.template.width, self.template.height, pre.transform.get("dpi_equivalent", 0.0))
        self.repo.add_page(page)

        # 2. segmentation
        t0 = time.perf_counter()
        seg = segment(pre.image, self.template, self.reference_path, self.cfg.get_path("segmentation", {}))
        stages["segment"] = time.perf_counter() - t0
        doc.metadata = {**doc.metadata, "signatures": seg.signatures, "alignment": {k: v for k, v in seg.alignment.items() if k != "anchors"}}
        if not seg.ok:
            self._move(doc, DocumentState.EN_REVISION, seg.cause)
            self.telemetry.event("alineacion_fuera_de_tolerancia", document_id=doc.id, cause=seg.cause)
            return ProcessOutcome(doc.id, doc.state.value, seg.cause, seconds=time.perf_counter() - t_start, stages=stages)
        crops: list[Crop] = []
        for ci in seg.crops:
            key, sha = None, None
            if ci.png is not None:
                sha = hashlib.sha256(ci.png).hexdigest()
                key = f"crops/{doc.id}/{ci.spec.id}-{sha[:16]}.png"
                self.storage.put(key, ci.png, {"document_id": doc.id, "field_id": ci.spec.id, "box_px": list(ci.box_px)})
            crops.append(Crop(new_id(), doc.id, page.id, ci.spec.id, self.template.version, ci.box_px, key, sha, ci.is_empty, ci.ink_ratio, ci.mark))
        crops = self.repo.upsert_crops(crops)
        if until == "segment":
            self.repo.update_document(doc)
            return ProcessOutcome(doc.id, doc.state.value, None, fields=sum(1 for c in crops if not c.is_empty), seconds=time.perf_counter() - t_start, stages=stages)

        # 3. recognition (only non-empty crops with ink)
        t0 = time.perf_counter()
        to_read = [c for c in crops if c.object_key and not c.is_empty and c.mark is None]
        spec_of = {f.id: f for f in self.template.fields}
        try:
            outputs = self._recognize_with_retry([(self.storage.get(c.object_key), spec_of[c.field_id].type.value) for c in to_read]) if to_read else []  # type: ignore[arg-type]
        except RecognizerUnavailable as exc:
            self._move(doc, DocumentState.ERROR, str(exc))
            return ProcessOutcome(doc.id, doc.state.value, str(exc), seconds=time.perf_counter() - t_start, stages=stages)
        stages["recognize"] = time.perf_counter() - t0
        recs = [
            Recognition(new_id(), c.id, o.model_version, o.text, o.score, o.raw_score, o.reliable, o.calibration_id, o.latency_ms)
            for c, o in zip(to_read, outputs, strict=True)
        ]
        self.repo.add_recognitions(recs)
        rec_by_crop = {r.crop_id: r for r in recs}

        # 4. structuring, rules, homologation and routing
        t0 = time.perf_counter()
        inputs = []
        for c in crops:
            spec = spec_of[c.field_id]
            r = rec_by_crop.get(c.id)
            inputs.append(FieldInput(spec, c.id, r.text if r else None, r.score if r else None, bool(r and r.reliable), r.id if r else None, c.mark, c.is_empty))
        codes = None
        if self.masters is not None:
            try:
                codes = self.masters.get_master("codigos").items
            except Exception:  # noqa: BLE001
                codes = None
        rule_cfg = RuleConfig(**{k: v for k, v in (self.cfg.get_path("rules", {}) or {}).items()})
        structured = structure(self.template, inputs, effective_code_sets(self.template, codes), self.homologator, self.routing_policy(), rule_cfg)
        stages["structure"] = time.perf_counter() - t0
        run_id = new_id()
        values = [self._field_value(doc.id, run_id, sf) for sf in structured]
        n_review = sum(1 for v in values if v.route == Route.REVIEW.value)
        with self.repo.transaction():
            self.repo.add_field_values(values)
            self._move(doc, DocumentState.PROCESADO, None)
            cause = f"{n_review} de {len(values)} campos requieren revisión" if n_review else f"0 de {len(values)} campos requieren revisión; confirme el registro"
            self._move(doc, DocumentState.EN_REVISION, cause)
            self.repo.add_audit(AuditEvent("document_processed", "document", doc.id, self.actor, after={"run_id": run_id, "fields": len(values), "review": n_review}))
        total = time.perf_counter() - t_start
        model_version = outputs[0].model_version if outputs else None
        self.telemetry.metric("tiempo_por_formato_s", total, model=model_version or "-")
        return ProcessOutcome(doc.id, doc.state.value, doc.state_cause, run_id, len(values), n_review, total, stages)

    @staticmethod
    def _field_value(doc_id: str, run_id: str, sf: StructuredField) -> FieldValue:
        assert sf.decision is not None
        return FieldValue(
            id=new_id(), document_id=doc_id, run_id=run_id, field_id=sf.inp.spec.id, field_type=sf.inp.spec.type.value,
            crop_id=sf.inp.crop_id, recognition_id=sf.inp.recognition_id, text=sf.inp.text, value=sf.value,
            confidence=sf.inp.confidence, route=sf.decision.route.value, reasons=list(sf.decision.reasons),
            violations=sf.violations, homologation=sf.homologation, mark=sf.inp.mark,
        )
