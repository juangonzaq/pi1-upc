"""HTTP driving adapter (FastAPI) used by the Next.js web interface.

It exposes the same application services as the CLI through the composition root: intake, processing with a
model version, manual review, approval, continuous learning and promotion. It adds no business rules; model
work is serialized with a lock and the learning cycle runs as a background job whose log the web polls.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import threading
import time
import traceback
import uuid
from collections import Counter, OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from perfo.bootstrap import Container
from perfo.domain.entities import Document
from perfo.domain.routing import describe
from perfo.domain.states import DocumentState
from perfo.ml.evaluate import same_value

BLOCKS = {"header": "Cabecera", "activities": "Actividades", "delays": "Demoras", "failures": "Fallas", "closing": "Cierre",
          "steel": "Control de aceros"}
JOB_NAMES = {"cycle": "reentrenamiento (ciclo de aprendizaje)"}
MAX_CROP_BYTES = 5 * 1024 * 1024
EXAMPLE_SAMPLE = 240  # crops compared in the evolution page
SAMPLE_BATCHES = ("operational", "demo")
# the web runs on 3100 (3000 is often taken by other local tools); both are allowed
DEFAULT_ORIGINS = ("http://localhost:3100", "http://127.0.0.1:3100", "http://localhost:3000", "http://127.0.0.1:3000")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(obj: Any) -> Any:
    """JSON-safe values: NaN/inf become null (metrics on empty sets), numpy scalars become Python numbers."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, np.floating):
        return _clean(float(obj))
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_clean(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(_clean(content), ensure_ascii=False, default=str).encode("utf-8")


# ---------------------------------------------------------------------------------------------------- request bodies
class ProcessBody(BaseModel):
    model: str | None = None
    user: str = "web"


class DecisionBody(BaseModel):
    action: Literal["confirm", "correct", "choose", "clear"]
    value: str | None = None
    text: str | None = None


class ValidateBody(BaseModel):
    user: str = Field(min_length=1)
    decisions: dict[str, DecisionBody] = Field(default_factory=dict)
    accept_rest: bool = False


class ApproveBody(BaseModel):
    decision: Literal["APROBADO", "OBSERVADO"]
    approver: str = Field(min_length=1)
    comment: str = ""


class PromoteBody(BaseModel):
    approver: str = Field(min_length=1)
    note: str = ""


class GenerateBody(BaseModel):
    n: int = Field(3, ge=1, le=8)


class BenchRunBody(BaseModel):
    crop_ids: list[str] = Field(min_length=1, max_length=200)
    versions: list[str] = Field(min_length=1, max_length=4)


# ---------------------------------------------------------------------------------------------------- state
@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"  # running | done | failed
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    log: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None

    def view(self, since: int = 0) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "status": self.status, "started_at": self.started_at, "finished_at": self.finished_at,
                "log": self.log[since:], "log_size": len(self.log), "result": self.result, "error": self.error}


class ApiState:
    def __init__(self, c: Container, reference: str | None):
        self.c = c
        self.reference = reference
        self.model_lock = threading.Lock()  # recognition, training and promotion share the device and the model cache
        self.write_lock = threading.RLock()  # intake and synthetic generation
        self.jobs: dict[str, Job] = {}
        self.spec_of = c.spec_of
        self._gt: Any = None
        self._masters: tuple[float, dict[str, dict[str, str]]] | None = None
        self._images: OrderedDict[tuple[str, int], bytes] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ jobs and locks
    def running_job(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.status == "running"), None)

    def ensure_idle(self) -> None:
        job = self.running_job()
        if job:
            raise HTTPException(409, f"Hay un {JOB_NAMES.get(job.kind, job.kind)} en curso; espere a que termine")

    @contextmanager
    def model(self, wait_s: float = 900) -> Iterator[None]:
        self.ensure_idle()
        if not self.model_lock.acquire(timeout=wait_s):
            raise HTTPException(503, "El modelo está ocupado; intente de nuevo en unos segundos")
        try:
            yield
        finally:
            self.model_lock.release()

    def release_models(self) -> None:
        """Frees cached recognizers except the active one (after a cycle or a promotion)."""
        active = self.c.registry.get_active()
        keep = f"{active.version}|True" if active else None
        self.c._recognizers = {k: v for k, v in self.c._recognizers.items() if k == keep}
        if hasattr(self.c, "_infer_cache"):
            self.c._infer_cache = {}

    # ------------------------------------------------------------ ground truth and masters
    def gt(self) -> Any:
        from perfo.ml.groundtruth import load_ground_truth  # noqa: PLC0415

        with self._lock:
            if self._gt is None:
                try:
                    self._gt = load_ground_truth(self.c.workspace, self.reference)
                except FileNotFoundError:
                    self._gt = False
            return self._gt or None

    def invalidate_gt(self) -> None:
        with self._lock:
            self._gt = None

    def gt_fields(self, doc: Document) -> dict[str, dict[str, Any]] | None:
        """Ground truth of a document (synthetic form id, or the file name in a double-entry reference CSV)."""
        gt = self.gt()
        if gt is None:
            return None
        for key in (doc.metadata.get("form_id"), doc.source_name):
            if key:
                f = gt.fields(key)
                if f:
                    return f
        return None

    def master_labels(self) -> dict[str, dict[str, str]]:
        now = time.monotonic()
        if self._masters is None or now - self._masters[0] > 30:
            out: dict[str, dict[str, str]] = {}
            for mtype in ("labores", "equipos", "personal", "codigos"):
                try:
                    out[mtype] = {i.code: i.description for i in self.c.masters.get_master(mtype).items}
                except Exception:  # noqa: BLE001 (master not imported yet)
                    out[mtype] = {}
            self._masters = (now, out)
        return self._masters[1]

    # ------------------------------------------------------------ images
    def jpeg(self, cache_key: str, loader: Any, width: int) -> bytes:
        k = (cache_key, width)
        with self._lock:
            if k in self._images:
                self._images.move_to_end(k)
                return self._images[k]
        img = cv2.imdecode(np.frombuffer(loader(), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(415, "La imagen no se puede mostrar (formato no soportado, p. ej. PDF)")
        h, w = img.shape[:2]
        if width and w > width:
            img = cv2.resize(img, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        data = buf.tobytes()
        with self._lock:
            self._images[k] = data
            while len(self._images) > 96:
                self._images.popitem(last=False)
        return data


# ---------------------------------------------------------------------------------------------------- helpers
def _doc_or_404(c: Container, doc_id: str) -> Document:
    doc = c.repo.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, f"Documento no encontrado: {doc_id}")
    return doc


def _check_model(c: Container, version: str | None) -> None:
    if version:
        try:
            c.registry.get(version)
        except KeyError as exc:
            raise HTTPException(404, f"Versión de modelo no registrada: {version}") from exc


def _doc_summary(d: Document, counts: dict[str, dict[str, int]]) -> dict[str, Any]:
    cnt = counts.get(d.id, {})
    return {"id": d.id, "form_id": d.metadata.get("form_id"), "source_name": d.source_name, "batch": d.metadata.get("batch"),
            "state": d.state.value, "cause": d.state_cause, "business_key": d.business_key, "uploaded_by": d.uploaded_by,
            "uploaded_at": d.uploaded_at, "fields": sum(cnt.values()), "review": cnt.get("REVIEW", 0), "auto": cnt.get("AUTO", 0),
            "rescan": bool(d.metadata.get("rescan")), "duplicate_of": d.duplicate_of}


def _process(st: ApiState, doc_id: str, model: str | None, user: str) -> dict[str, Any]:
    c = st.c
    _check_model(c, model)
    with st.model():
        doc = _doc_or_404(c, doc_id)
        if doc.state == DocumentState.DUPLICADO_PENDIENTE:
            raise HTTPException(409, "El documento es un posible duplicado; confírmelo antes de procesarlo")
        if doc.state in (DocumentState.CARGADO, DocumentState.ERROR, DocumentState.EN_REVISION):
            c.ingestion.enqueue(doc_id, user)
        elif doc.state != DocumentState.EN_COLA:
            raise HTTPException(409, f"El documento está en estado {doc.state.value}; ya no se reprocesa")
        if model is None:
            c.ensure_baseline()
        version = model or c.registry.get_active().version
        out = c.processor(actor=user, version=model).process(doc_id)
    return {"document_id": doc_id, "state": out.state, "cause": out.cause, "seconds": out.seconds, "stages": out.stages,
            "fields": out.fields, "review": out.review, "model": version}


def _ingest(st: ApiState, data: bytes, meta: dict[str, Any], model: str | None, process: bool) -> dict[str, Any]:
    from perfo.application.ingest import IngestionRejected  # noqa: PLC0415

    c = st.c
    if process:
        st.ensure_idle()
        _check_model(c, model)
    with st.write_lock:
        try:
            doc_id = c.ingestion.submit(data, meta)
        except IngestionRejected as exc:
            raise HTTPException(exc.code, str(exc)) from exc
        outcome = c.ingestion.last_outcome
    res: dict[str, Any] = {"document_id": doc_id, "outcome": outcome}
    retry = process and outcome == "repetida" and _doc_or_404(c, doc_id).state == DocumentState.ERROR  # a repeated upload retries what failed
    if process and (outcome == "nuevo" or retry):
        res["processing"] = _process(st, doc_id, model, meta.get("uploaded_by", "web"))
    if retry:
        res["outcome"] = "reintentada"
    doc = _doc_or_404(c, doc_id)
    res.update(state=doc.state.value, cause=doc.state_cause)
    return res


def _homologation(h: Any, labels: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    if h is None:
        return None
    names = labels.get(h.master_type, {})
    return {"master_type": h.master_type, "input": h.input_text, "normalized": h.normalized, "code": h.code,
            "description": names.get(h.code) if h.code else None, "score": h.score, "method": h.method, "accepted": h.accepted,
            "candidates": [{"code": x["code"], "description": x.get("description") or names.get(x["code"]), "score": x["score"]} for x in h.candidates]}


def _thresholds(c: Container, version: str) -> dict[str, float | None]:
    try:
        return (c.calibration_profile(version) or {}).get("thresholds", {})
    except KeyError:
        return {}


def _document_detail(st: ApiState, doc: Document) -> dict[str, Any]:
    c = st.c
    values = c.repo.get_field_values(doc.id)
    crops = {cr.id: cr for cr in c.repo.get_crops(doc.id)}
    recs = {r.id: r for r in c.repo.get_recognitions([v.crop_id for v in values if v.crop_id])} if values else {}
    records = c.repo.get_records(doc.id)
    validated = {v.field_id: v for v in records[-1].values} if records else {}
    truth = st.gt_fields(doc)
    labels = st.master_labels()
    fields = []
    specs = {f.id: f for f in c.template_for(doc.template_version).fields}  # the box of a field depends on the form the document was read with
    for v in values:
        spec = specs.get(v.field_id, st.spec_of[v.field_id])
        crop = crops.get(v.crop_id) if v.crop_id else None
        rec = recs.get(v.recognition_id) if v.recognition_id else None
        g = (truth.get(v.field_id) or {"value": None, "text": None, "mark": None}) if truth is not None else None
        vv = validated.get(v.field_id)
        fields.append({
            "id": v.field_id, "label": spec.label, "block": spec.block, "block_label": BLOCKS.get(spec.block, spec.block),
            "table": spec.table, "row": spec.row, "col": spec.col, "type": v.field_type, "master_type": spec.master_type,
            "code_set": spec.code_set, "required": spec.required, "box": list(spec.box),
            "text": v.text, "value": v.value, "value_label": labels.get(spec.master_type or "", {}).get(v.value or ""),
            "confidence": v.confidence, "raw_score": rec.raw_score if rec else None, "reliable": rec.reliable if rec else None,
            "model_version": rec.model_version if rec else None, "route": v.route,
            "reasons": [{"code": r, "text": describe(r)} for r in v.reasons], "violations": v.violations,
            "homologation": _homologation(v.homologation, labels), "mark": v.mark,
            "empty": bool(crop is not None and crop.is_empty), "crop": crop.object_key if crop and crop.object_key else None,
            "validated": {"value": vv.value, "source": vv.source} if vv else None,
            "truth": {"value": g["value"], "text": g.get("text"), "mark": g.get("mark")} if g is not None else None,
            "correct": same_value(v.field_type, v.value, g["value"]) if g is not None else None,
        })
    rec_any = next(iter(recs.values()), None)
    with_truth = [f for f in fields if f["truth"] is not None and (f["truth"]["value"] is not None or f["value"] is not None)]
    summary = {
        "fields": len(fields), "auto": sum(f["route"] == "AUTO" for f in fields), "review": sum(f["route"] == "REVIEW" for f in fields),
        "read": len(recs), "by_type": dict(Counter(f["type"] for f in fields if f["route"] == "REVIEW")),
        "truth_available": truth is not None, "truth_compared": len(with_truth), "truth_correct": sum(bool(f["correct"]) for f in with_truth),
        "auto_errors": sum(1 for f in with_truth if f["route"] == "AUTO" and not f["correct"]),
    }
    return {
        "document": {**_doc_summary(doc, {}), "sha256": doc.sha256, "template_version": doc.template_version,
                     "content_type": doc.content_type, "has_preprocessed": bool(doc.preprocessed_key),
                     "transform": {k: v for k, v in (doc.transform or {}).items() if k != "homography"},
                     "alignment": doc.metadata.get("alignment"), "signatures": doc.metadata.get("signatures"),
                     "quality_warnings": doc.metadata.get("quality_warnings") or []},
        "run": {"run_id": values[0].run_id, "model_version": rec_any.model_version if rec_any else None,
                "calibration_id": rec_any.calibration_id if rec_any else None,
                "thresholds": _thresholds(c, rec_any.model_version) if rec_any else {}} if values else None,
        "summary": summary,
        "fields": fields,
        "history": c.repo.state_history(doc.id),
        "records": [{"id": r.id, "version": r.version, "validated_by": r.validated_by, "validated_at": r.validated_at, "sha256": r.sha256,
                     "stats": r.stats} for r in records],
        "approvals": [{"decision": a.decision, "approver": a.approver, "comment": a.comment, "at": a.created_at}
                      for a in c.repo.get_approvals(doc.id)],
    }


def _model_metrics(c: Container, version: str) -> dict[str, Any]:
    """Test-set metrics of a version, taken from its own gate evaluation or from the evaluation attached to a child."""
    owners = [version] + [i.version for i in c.registry.list() if i.version != version]
    for owner in owners:
        for name in ("gates", "evaluation"):
            att = c.registry.read_attachment(owner, name)
            m = (att.get("evaluation", att).get("models", {}) if att else {}).get(version)
            if m:
                found = {k: m.get(k) for k in ("cer", "wer", "exact", "field_accuracy", "cer_unseen_operators", "n")}
                h = m.get("homologation") or {}
                found["homologation"] = {k: h.get(k) for k in ("acc_at_1", "recall_at_3")}
                return found
    return {}


def _version_kind(version: str, base: str) -> tuple[str, str, str]:
    """(kind, label, short label) of a version, in the words of the evolution page."""
    if version == base:
        return "base", "Modelo base", "Base"
    if version.startswith("cycle"):
        n = int(version[5:7])
        return "cycle", f"Reentrenamiento {n}", f"R{n}"
    return "initial", "Ajuste inicial", "Ajuste"


def _evolution(c: Container) -> dict[str, Any]:
    """Every version measured on one same set of forms: comparing versions across different sets would mean nothing."""
    from perfo.ml.pipeline import baseline_version  # noqa: PLC0415

    sets: list[dict[str, Any]] = []
    for path in sorted((c.workspace / "reports").glob("evaluacion_referencia_*.json")):
        report = json.loads(path.read_text())
        sets.append({"documents": report.get("documents"), "models": report.get("models", {})})
    frozen: dict[str, Any] = {}
    for info in c.registry.list():  # the frozen test set: each gate evaluation measures the active version and its candidate
        gates = c.registry.read_attachment(info.version, "gates")
        frozen.update((gates or {}).get("evaluation", {}).get("models", {}))
    if frozen:
        sets.append({"documents": None, "models": frozen})
    base = baseline_version(c)
    active = c.registry.get_active()
    infos = sorted(c.registry.list(), key=lambda i: (i.version != base, i.created_at))
    order = [i.version for i in infos]
    real = bool(c.cfg.get_path("real_handwriting", False))
    if not sets:
        return {"points": [], "documents": None, "real_handwriting": real}

    def coverage(s: dict[str, Any]) -> tuple[int, int]:
        measured = [order.index(v) for v in s["models"] if v in order]
        return len(measured), max(measured, default=-1)

    best = max(sets, key=coverage)
    points = []
    for info in infos:
        m = best["models"].get(info.version)
        if m is None:
            continue
        kind, label, short = _version_kind(info.version, base)
        by_type = {t: {"accuracy": max(0.0, 1 - s["cer"]), "cer": s["cer"], "exact": s["exact"], "n": s["n"]} for t, s in m["by_type"].items()}
        points.append({"version": info.version, "kind": kind, "label": label, "short": short, "status": info.status, "active": active is not None and active.version == info.version,
                       "has_weights": c.has_weights(info.version), "created_at": info.created_at, "accuracy": max(0.0, 1 - m["cer"]), "cer": m["cer"], "exact": m["exact"],
                       "n": m.get("n"), "by_type": by_type})
    return {"points": points, "documents": best["documents"], "real_handwriting": real}


def _model_view(c: Container, info: Any, active: str | None, history: list[dict[str, Any]]) -> dict[str, Any]:
    from perfo.ml.learning_cycle import CYCLE_REQUIRED, INITIAL_REQUIRED  # noqa: PLC0415

    cal = c.registry.read_attachment(info.version, "calibration")
    gates = c.registry.read_attachment(info.version, "gates")
    cycle = c.registry.read_attachment(info.version, "cycle")
    routing = (gates or {}).get("routing") or c.registry.read_attachment(info.version, "routing_test")
    training = info.metadata.get("training") or {}
    required = CYCLE_REQUIRED if info.parent and not str(info.parent).startswith("baseline-") else INITIAL_REQUIRED
    return {
        "version": info.version, "status": info.status, "active": info.version == active, "parent": info.parent,
        "created_at": info.created_at, "base_checkpoint": info.base_checkpoint, "artifact_sha256": info.artifact_sha256,
        "description": info.metadata.get("description"), "dataset_snapshot": info.metadata.get("dataset_snapshot"),
        "training": {k: training.get(k) for k in ("best_step", "steps", "seconds", "initial_select_cer", "best_select_cer")} if training else None,
        "calibration": {"id": cal.get("id"), "temperatures": cal.get("temperatures"), "thresholds": cal.get("thresholds"),
                        "created_at": cal.get("created_at")} if cal else None,
        "gates": gates.get("gates") if gates else None, "gates_against": gates.get("active") if gates else None,
        "relative_cer_reduction_pct": gates.get("relative_cer_reduction_pct") if gates else None,
        "required_gates": list(required),
        "routing": {k: routing.get(k) for k in ("coverage", "residual_risk", "ece", "n_fields", "auto")} if routing else None,
        "metrics": _model_metrics(c, info.version),
        "cycle": {k: cycle.get(k) for k in ("snapshot", "stats", "ie5_pct", "status")} if cycle else None,
        "promotions": [h for h in history if h.get("version") == info.version],
        "status_log": info.metadata.get("status_log") or [],
    }


def _corrections_overview(c: Container) -> dict[str, Any]:
    from perfo.ml.learning_cycle import protected_docs  # noqa: PLC0415
    from perfo.ml.pipeline import SPLIT_VERSION  # noqa: PLC0415

    split = c.repo.get_split(SPLIT_VERSION) or {}
    prot = protected_docs(split)
    corrs = c.repo.list_corrections(unused_only=False)
    usable = [x for x in corrs if x.document_id not in prot]
    by_kind = Counter(x.kind for x in usable)
    last_cycle = max((s for s in c.repo.list_snapshots() if s["kind"] == "cycle"), key=lambda s: s["id"], default=None)
    return {"corrections": by_kind.get("correction", 0), "confirmed_review": by_kind.get("confirmed_review", 0),
            "confirmed_auto": by_kind.get("confirmed_auto", 0), "excluded_protected": len(corrs) - len(usable),
            "documents": len({x.document_id for x in usable}), "split_ready": bool(split), "last_cycle": last_cycle}


def _sample_entries(c: Container) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    synth = c.workspace / "synth"
    man_path = synth / "manifest.json"
    if not man_path.exists():
        return None, []
    man = json.loads(man_path.read_text())
    out = [{**f, "sample_id": f["form_id"], "rescan": False} for f in man["forms"] if f["batch"] in SAMPLE_BATCHES]
    if man.get("rescan"):
        r = man["rescan"]
        src = next((f for f in man["forms"] if f["form_id"] == r["form_id"]), None)
        if src:
            sha = hashlib.sha256((synth / r["image"]).read_bytes()).hexdigest()
            out.append({**src, "sample_id": f"{r['form_id']}_reescaneo", "image": r["image"], "image_sha256": sha, "rescan": True})
    return man, out


# ---------------------------------------------------------------------------------------------------- application
def create_app(c: Container, reference: str | None = None, origins: list[str] | None = None) -> FastAPI:  # noqa: C901, PLR0915
    st = ApiState(c, reference)
    app = FastAPI(title="Perforación IA · API", version="0.1.0", default_response_class=SafeJSONResponse,
                  description="Adaptador HTTP del núcleo: ingesta, reconocimiento, revisión manual y reentrenamiento.")
    app.state.perfo = st

    @app.middleware("http")
    async def _errors(request: Any, call_next: Any) -> Any:
        # inside the CORS middleware, so the browser receives the message instead of a CORS failure
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exception(exc)
            return SafeJSONResponse({"detail": f"Error interno ({type(exc).__name__}): {exc}"}, status_code=500)

    app.add_middleware(CORSMiddleware, allow_origins=origins or list(DEFAULT_ORIGINS),
                       allow_methods=["*"], allow_headers=["*"])

    # ------------------------------------------------------------ status
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        from perfo.application.ingest import MAX_BYTES  # noqa: PLC0415

        active = c.registry.get_active()
        cal = c.calibration_profile(active.version) if active else None
        labels = st.master_labels()
        job = st.running_job()
        return {
            "ok": True, "workspace": str(c.workspace), "profile": c.cfg.get("profile"), "device": c.device(),
            "active_model": active.version if active else None, "active_calibrated": bool(cal),
            "masters": {k: len(v) for k, v in labels.items()}, "synthetic": (c.workspace / "synth" / "manifest.json").exists(),
            "ground_truth": st.gt() is not None, "reference_csv": reference,
            "split": bool(c.repo.get_split("split-v1")), "busy": job.view(len(job.log)) if job else None,
            "upload": {"max_bytes": MAX_BYTES, "reference_long_px": max(c.template.width, c.template.height), "template_dpi": c.template.dpi,
                       "recommended_dpi": c.template.alignment.min_dpi,
                       "min_dpi": float(c.cfg.get_path("preprocessing", {}).get("min_dpi", c.template.alignment.min_dpi))},
        }

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        docs = c.repo.list_documents()
        counts = c.repo.field_value_counts()
        active = c.registry.get_active()
        cal = c.calibration_profile(active.version) if active else None
        tot = Counter()
        for d in docs:
            tot.update(counts.get(d.id, {}))
        return {
            "documents": len(docs), "by_state": dict(Counter(d.state.value for d in docs)),
            "by_batch": dict(Counter(d.metadata.get("batch") or "-" for d in docs)),
            "fields": {"auto": tot.get("AUTO", 0), "review": tot.get("REVIEW", 0)},
            "active_model": {"version": active.version, "status": active.status, "parent": active.parent,
                             "thresholds": (cal or {}).get("thresholds"), "temperatures": (cal or {}).get("temperatures")} if active else None,
            "learning": _corrections_overview(c),
            "models": len(c.registry.list()),
        }

    # ------------------------------------------------------------ synthetic samples (demo with known ground truth)
    @app.get("/api/samples")
    def samples() -> dict[str, Any]:
        man, entries = _sample_entries(c)
        if man is None:
            return {"available": False, "samples": []}
        by_sha = {d.sha256: d for d in c.repo.list_documents()}
        out = []
        for f in entries:
            d = by_sha.get(f.get("image_sha256"))
            out.append({"sample_id": f["sample_id"], "form_id": f["form_id"], "batch": f["batch"], "operator": f.get("operator"),
                        "new_operator": f.get("new_operator"), "bad_scan": f.get("bad_scan"), "business_key": f.get("metadata_key"),
                        "rescan": f["rescan"], "document": {"id": d.id, "state": d.state.value} if d else None})
        return {"available": True, "samples": out}

    def _sample(sample_id: str) -> dict[str, Any]:
        _, entries = _sample_entries(c)
        f = next((x for x in entries if x["sample_id"] == sample_id), None)
        if f is None:
            raise HTTPException(404, f"Formato sintético no encontrado: {sample_id}")
        return f

    @app.get("/api/samples/{sample_id}/image")
    def sample_image(sample_id: str, w: int = Query(480, ge=64, le=3600)) -> Response:
        f = _sample(sample_id)
        path = (c.workspace / "synth" / f["image"]).resolve()
        if (c.workspace / "synth").resolve() not in path.parents:
            raise HTTPException(400, "Ruta inválida")
        return Response(st.jpeg(f"synth:{path}:{path.stat().st_mtime}", path.read_bytes, w), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/samples/{sample_id}/process")
    def sample_process(sample_id: str, body: ProcessBody) -> dict[str, Any]:
        f = _sample(sample_id)
        path = c.workspace / "synth" / f["image"]
        meta = {"source_name": path.name, "uploaded_by": body.user, "business_key": f["metadata_key"], "batch": f["batch"], "form_id": f["form_id"]}
        if f["rescan"]:
            meta["rescan"] = True
        return _ingest(st, path.read_bytes(), meta, body.model, process=True)

    @app.post("/api/samples/generate")
    def samples_generate(body: GenerateBody) -> dict[str, Any]:
        from perfo.synth.generator import generate_extra_forms  # noqa: PLC0415

        if not (c.workspace / "synth" / "manifest.json").exists():
            raise HTTPException(409, "El espacio de trabajo no tiene corpus sintético (ejecute perfo synth o perfo e2e)")
        t0 = time.perf_counter()
        with st.write_lock:
            entries = generate_extra_forms(c.workspace / "synth", c.template_definition, c.cfg.resolve("data/fonts"), body.n)
        st.invalidate_gt()
        return {"generated": [e["form_id"] for e in entries], "seconds": time.perf_counter() - t0}

    # ------------------------------------------------------------ intake and processing
    @app.post("/api/documents")
    def upload(
        file: UploadFile = File(...), user: str = Form("web"), fecha: str | None = Form(None), guardia: str | None = Form(None),
        equipo: str | None = Form(None), model: str | None = Form(None), process: bool = Form(True),
    ) -> dict[str, Any]:
        from perfo.domain.parsing import parse_date  # noqa: PLC0415

        data = file.file.read()
        name = Path(file.filename or "formato").name
        stem = Path(name).stem
        gt = st.gt()
        form_id = stem if gt is not None and gt.fields(stem) else name  # a synthetic form uploaded by hand keeps its ground truth
        meta: dict[str, Any] = {"source_name": name, "uploaded_by": user or "web", "form_id": form_id, "batch": "web"}
        if fecha and guardia and equipo:
            d = parse_date(fecha)
            meta["business_key"] = f"{d.value if d.ok else fecha}|{guardia.strip().upper()}|{equipo.strip()}"
        return _ingest(st, data, meta, model or None, process)

    @app.post("/api/documents/{doc_id}/process")
    def document_process(doc_id: str, body: ProcessBody) -> dict[str, Any]:
        return _process(st, doc_id, body.model, body.user)

    @app.post("/api/documents/{doc_id}/confirm-duplicate")
    def confirm_duplicate(doc_id: str, body: ProcessBody) -> dict[str, Any]:
        doc = _doc_or_404(c, doc_id)
        if doc.state != DocumentState.DUPLICADO_PENDIENTE:
            raise HTTPException(409, f"El documento no está pendiente de confirmación (estado {doc.state.value})")
        st.ensure_idle()
        with st.write_lock:
            c.ingestion.confirm_duplicate(doc_id, body.user)
        return _process(st, doc_id, body.model, body.user)

    @app.get("/api/documents")
    def documents(state: str | None = None, batch: str | None = None, q: str | None = None, limit: int = Query(300, ge=1, le=5000)) -> dict[str, Any]:
        states = [s.strip() for s in state.split(",")] if state else None
        docs = c.repo.list_documents(states=states)
        if batch:
            docs = [d for d in docs if (d.metadata.get("batch") or "") == batch]
        if q:
            ql = q.lower()
            docs = [d for d in docs if ql in d.id or ql in (d.metadata.get("form_id") or "").lower() or ql in d.source_name.lower()
                    or ql in (d.business_key or "").lower()]
        docs.sort(key=lambda d: d.uploaded_at, reverse=True)
        counts = c.repo.field_value_counts()
        return {"total": len(docs), "documents": [_doc_summary(d, counts) for d in docs[:limit]]}

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: str) -> dict[str, Any]:
        return _document_detail(st, _doc_or_404(c, doc_id))

    @app.get("/api/documents/{doc_id}/image")
    def document_image(doc_id: str, kind: Literal["preprocessed", "original"] = "preprocessed", w: int = Query(1600, ge=64, le=3600)) -> Response:
        doc = _doc_or_404(c, doc_id)
        key = doc.preprocessed_key if kind == "preprocessed" else doc.original_key
        if not key:
            raise HTTPException(404, "El documento no tiene imagen preprocesada (falló el control de calidad o aún no se procesa)")
        return Response(st.jpeg(key, lambda: c.storage.get(key), w), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/objects/{key:path}")
    def object_bytes(key: str) -> Response:
        if not key.startswith("crops/"):
            raise HTTPException(403, "Solo se sirven recortes")
        try:
            data = c.storage.get(key)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, "Objeto no encontrado") from exc
        return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=31536000, immutable"})

    # ------------------------------------------------------------ manual review, approval and traceability
    @app.get("/api/catalogs")
    def catalogs() -> dict[str, Any]:
        from perfo.application.structuring import effective_code_sets  # noqa: PLC0415

        labels = st.master_labels()
        try:
            codes = c.masters.get_master("codigos").items
        except Exception:  # noqa: BLE001
            codes = None
        sets = effective_code_sets(c.template, codes)
        for t, _ in c.alternative_templates:
            sets = {**effective_code_sets(t, codes), **sets}
        return {
            "masters": {m: [{"code": k, "description": v} for k, v in labels.get(m, {}).items()] for m in ("labores", "equipos", "personal")},
            "code_sets": {name: [{"code": str(v), "description": labels["codigos"].get(str(v), "")} for v in d.get("values", [])]
                          for name, d in sets.items()},
        }

    @app.post("/api/documents/{doc_id}/validate")
    def validate(doc_id: str, body: ValidateBody) -> Any:
        from perfo.application.validation import Decision, ValidationBlocked  # noqa: PLC0415

        _doc_or_404(c, doc_id)
        decisions = {k: Decision(d.action, (d.value or "").strip() or None, (d.text or "").strip() or None) for k, d in body.decisions.items()}
        try:
            with st.write_lock:
                res = c.validation.confirm(doc_id, decisions, body.user.strip(), accept_rest=body.accept_rest)
        except ValidationBlocked as exc:
            return SafeJSONResponse({"detail": str(exc), "fields": exc.fields}, status_code=422)
        return {"record": {"id": res.record.id, "version": res.record.version, "sha256": res.record.sha256}, "stats": res.stats,
                "touched": res.touched, "corrected": res.corrected, "aliases": res.aliases}

    @app.post("/api/documents/{doc_id}/approve")
    def approve(doc_id: str, body: ApproveBody) -> Any:
        from perfo.application.validation import ValidationBlocked  # noqa: PLC0415

        _doc_or_404(c, doc_id)
        try:
            with st.write_lock:
                ap = c.validation.approve(doc_id, body.decision, body.approver.strip(), body.comment.strip())
        except ValidationBlocked as exc:
            return SafeJSONResponse({"detail": str(exc), "fields": exc.fields}, status_code=422)
        return {"decision": ap.decision, "approver": ap.approver, "comment": ap.comment, "at": ap.created_at}

    @app.get("/api/documents/{doc_id}/trace/{field_id}")
    def trace(doc_id: str, field_id: str) -> dict[str, Any]:
        _doc_or_404(c, doc_id)
        if field_id not in st.spec_of:
            raise HTTPException(404, f"Campo inexistente: {field_id}")
        return c.validation.trace(doc_id, field_id)

    # ------------------------------------------------------------ crop bench: the recognizer alone, with ground truth
    def _origins() -> dict[str, str]:
        from perfo.ml.pipeline import SPLIT_VERSION  # noqa: PLC0415

        split = c.repo.get_split(SPLIT_VERSION) or {}
        out = {}
        for k in ("train", "val_select", "val_calib", "test"):
            for d in split.get(k, []):
                out[d] = k
        return out

    @app.get("/api/bench/crops")
    def bench_crops(
        field_type: str | None = None, n: int = Query(16, ge=1, le=96), seed: int | None = None,
        source: Literal["no_vistos", "entrenamiento", "todos"] = "no_vistos",
    ) -> dict[str, Any]:
        from perfo.ml.metrics import literal  # noqa: PLC0415

        origins = _origins()
        allowed = {"no_vistos": {"test", "operational", "demo", "web"}, "entrenamiento": {"train"}}.get(source)
        docs = []
        for d in c.repo.list_documents():
            origin = origins.get(d.id) or d.metadata.get("batch") or "web"
            if (allowed is None or origin in allowed) and d.preprocessed_key:
                docs.append((d, origin))
        rng = random.Random(seed if seed is not None else time.time_ns())
        rng.shuffle(docs)
        items: list[dict[str, Any]] = []
        for d, origin in docs[:60]:
            if len(items) >= 4 * n:
                break
            truth = st.gt_fields(d)
            for cr in c.repo.get_crops(d.id):
                spec = st.spec_of.get(cr.field_id)
                if spec is None or cr.is_empty or cr.mark or not cr.object_key or spec.signature:
                    continue
                if field_type and spec.type.value != field_type:
                    continue
                g = (truth or {}).get(cr.field_id) or {}
                items.append({"crop_id": cr.id, "crop": cr.object_key, "field_id": cr.field_id, "label": spec.label,
                              "block_label": BLOCKS.get(spec.block, spec.block), "row": spec.row, "type": spec.type.value,
                              "document_id": d.id, "form_id": d.metadata.get("form_id"), "origin": origin,
                              "truth": literal(g["text"]) if g.get("text") else None})
        rng.shuffle(items)
        return {"crops": items[:n]}

    def _bench(images: list[tuple[bytes, str]], versions: list[str], truths: list[str | None]) -> dict[str, Any]:
        from perfo.ml.metrics import cer, literal  # noqa: PLC0415

        for v in versions:
            _check_model(c, v)
        out = {}
        with st.model():
            for v in versions:
                rec = c.recognizer(v)
                thresholds = (c.calibration_profile(v) or {}).get("thresholds", {})
                t0 = time.perf_counter()
                outs = rec.recognize_batch(images)
                secs = time.perf_counter() - t0
                rows = []
                for (_, ftype), o, ref in zip(images, outs, truths, strict=True):
                    thr = thresholds.get(ftype)
                    if not o.reliable:
                        route, why = "REVIEW", "unreliable_confidence"
                    elif thr is None:
                        route, why = "REVIEW", "no_threshold"
                    elif o.score is not None and o.score >= thr:
                        route, why = "AUTO", None
                    else:
                        route, why = "REVIEW", "low_confidence"
                    rows.append({"text": o.text, "score": o.score, "raw_score": o.raw_score, "reliable": o.reliable, "threshold": thr,
                                 "route": route, "reason": describe(why) if why else None,
                                 "correct": (literal(o.text) == ref) if ref is not None else None})
                pairs = [(ref, literal(r["text"])) for r, ref in zip(rows, truths, strict=True) if ref is not None]
                auto = [r for r in rows if r["route"] == "AUTO"]
                out[v] = {"rows": rows, "seconds": secs, "ms_per_crop": 1000 * secs / max(1, len(rows)),
                          "summary": {"n": len(rows), "with_truth": len(pairs),
                                      "exact": (sum(r["correct"] for r in rows if r["correct"] is not None) / len(pairs)) if pairs else None,
                                      "cer": cer([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None,
                                      "auto": len(auto), "auto_errors": sum(1 for r in auto if r["correct"] is False)}}
        return out

    @app.post("/api/bench/run")
    def bench_run(body: BenchRunBody) -> dict[str, Any]:
        from perfo.ml.metrics import literal  # noqa: PLC0415

        crops = []
        for cid in body.crop_ids:
            cr = c.repo.get_crop(cid)
            if cr is None or not cr.object_key:
                raise HTTPException(404, f"Recorte no encontrado: {cid}")
            crops.append(cr)
        docs = {cr.document_id: c.repo.get_document(cr.document_id) for cr in crops}
        truths = []
        for cr in crops:
            g = (st.gt_fields(docs[cr.document_id]) or {}).get(cr.field_id) or {}
            truths.append(literal(g["text"]) if g.get("text") else None)
        images = [(c.storage.get(cr.object_key), st.spec_of[cr.field_id].type.value) for cr in crops]
        return {"crop_ids": body.crop_ids, "results": _bench(images, body.versions, truths)}

    @app.post("/api/bench/upload")
    def bench_upload(file: UploadFile = File(...), field_type: str = Form(...), versions: str = Form(...), truth: str | None = Form(None)) -> dict[str, Any]:
        from perfo.domain.fields import FieldType  # noqa: PLC0415
        from perfo.ml.metrics import literal  # noqa: PLC0415

        if field_type not in {t.value for t in FieldType}:
            raise HTTPException(422, f"Tipo de campo desconocido: {field_type}")
        data = file.file.read(MAX_CROP_BYTES + 1)
        if len(data) > MAX_CROP_BYTES:
            raise HTTPException(413, "El recorte excede 5 MB")
        if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
            raise HTTPException(415, "La imagen no se puede leer (use PNG o JPG)")
        vs = [v.strip() for v in versions.split(",") if v.strip()][:4]
        if not vs:
            raise HTTPException(422, "Indique al menos una versión de modelo")
        return {"results": _bench([(data, field_type)], vs, [literal(truth) if truth else None])}

    # ------------------------------------------------------------ evolution of the model
    @app.get("/api/evolution")
    def evolution() -> dict[str, Any]:
        return _evolution(c)

    @app.get("/api/evolution/examples")
    def evolution_examples() -> dict[str, Any]:
        """The same crops read by the first and the last version, chosen at random so the balance is not cherry-picked."""
        from perfo.ml.metrics import literal  # noqa: PLC0415

        points = [p for p in _evolution(c)["points"] if p["status"] != "rejected"]
        if len(points) < 2:
            return {"available": False}
        first, last = points[0], points[-1]
        cache = c.workspace / "reports" / f"evolucion_ejemplos_{first['version']}_{last['version']}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        pool = []
        for d in c.repo.list_documents():
            truth = st.gt_fields(d) or {}
            for cr in c.repo.get_crops(d.id):
                spec = st.spec_of.get(cr.field_id)
                text = (truth.get(cr.field_id) or {}).get("text")
                if spec is not None and text and cr.object_key and not cr.is_empty and not cr.mark and not spec.signature:
                    pool.append((cr, spec, literal(text)))
        if not pool:
            return {"available": False}
        rng = random.Random(1234)
        sample = rng.sample(pool, k=min(EXAMPLE_SAMPLE, len(pool)))

        def readings(version: str) -> dict[str, str]:
            read = {r.crop_id: r.text for r in c.repo.get_recognitions([cr.id for cr, _, _ in sample], version)}
            missing = [(cr, spec) for cr, spec, _ in sample if cr.id not in read]
            if missing and c.has_weights(version):
                with st.model():
                    outs = c.recognizer(version).recognize_batch([(c.storage.get(cr.object_key), spec.type.value) for cr, spec in missing])
                read.update({cr.id: o.text for (cr, _), o in zip(missing, outs, strict=True)})
            return read

        before, after = readings(first["version"]), readings(last["version"])
        rows = []
        for cr, spec, truth in sample:
            if cr.id in before and cr.id in after:
                was, now = literal(before[cr.id]) == truth, literal(after[cr.id]) == truth
                verdict = "same_right" if was and now else "improved" if now else "worse" if was else "same_wrong"
                rows.append({"crop": cr.object_key, "type": spec.type.value, "label": spec.label, "truth": truth, "before": literal(before[cr.id]), "after": literal(after[cr.id]), "verdict": verdict})
        if not rows:
            return {"available": False}
        by_type: dict[str, list[dict[str, Any]]] = {}
        for r in sorted((r for r in rows if r["verdict"] == "improved"), key=lambda r: -len(r["truth"])):
            by_type.setdefault(r["type"], []).append(r)
        improved = [r for group in by_type.values() for r in group[:2]][:6]
        result = {"available": True, "from": first["label"], "to": last["label"], "n": len(rows), "counts": dict(Counter(r["verdict"] for r in rows)),
                  "examples": improved + [r for r in rows if r["verdict"] == "worse"][:2]}
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=1))
        return result

    # ------------------------------------------------------------ models, learning cycle and promotion
    @app.get("/api/models")
    def models() -> dict[str, Any]:
        active = c.registry.get_active()
        history = c.registry.history() if hasattr(c.registry, "history") else []
        return {"active": active.version if active else None,
                "models": [_model_view(c, i, active.version if active else None, history) for i in reversed(c.registry.list())],
                "history": history, "learning": _corrections_overview(c)}

    @app.post("/api/cycle")
    def cycle_start() -> dict[str, Any]:
        from perfo.ml.learning_cycle import run_cycle  # noqa: PLC0415

        st.ensure_idle()
        if not c.repo.get_split("split-v1"):
            raise HTTPException(409, "No hay partición congelada; construya el dataset y entrene el primer modelo (perfo e2e)")
        gt = st.gt()
        if gt is None:
            raise HTTPException(409, "Sin verdad de referencia para evaluar (corpus sintético o CSV --reference)")
        job = Job(uuid.uuid4().hex[:12], "cycle")
        st.jobs[job.id] = job
        acquired = st.model_lock.acquire(timeout=5)
        if not acquired:
            job.status, job.error, job.finished_at = "failed", "El modelo está ocupado procesando; reintente", _now()
            raise HTTPException(503, job.error)

        def log(msg: Any) -> None:
            job.log.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")

        def worker() -> None:
            try:
                log("Inicio del ciclo: snapshot de correcciones → reentrenamiento con replay → recalibración → compuertas G1–G5")
                res = run_cycle(c, gt, log=log)
                job.result = {k: res.get(k) for k in ("status", "candidate", "snapshot", "stats", "ie5_pct", "gates", "latency", "active_before", "error")}
                log(f"Estado del ciclo: {res.get('status')}")
                job.status = "done"
            except Exception as exc:  # noqa: BLE001
                job.error = f"{type(exc).__name__}: {exc}"
                log(f"Error: {job.error}")
                traceback.print_exc()
                job.status = "failed"
            finally:
                job.finished_at = _now()
                st.release_models()
                st.model_lock.release()

        threading.Thread(target=worker, name=f"cycle-{job.id}", daemon=True).start()
        return job.view()

    @app.get("/api/jobs")
    def jobs() -> dict[str, Any]:
        return {"jobs": [j.view(max(0, len(j.log) - 1)) for j in sorted(st.jobs.values(), key=lambda j: j.started_at, reverse=True)]}

    @app.get("/api/jobs/{job_id}")
    def job_view(job_id: str, since: int = Query(0, ge=0)) -> dict[str, Any]:
        job = st.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Trabajo no encontrado (el servidor pudo reiniciarse)")
        return job.view(since)

    @app.post("/api/models/{version}/promote")
    def promote_model(version: str, body: PromoteBody) -> dict[str, Any]:
        from perfo.ml.learning_cycle import promote  # noqa: PLC0415

        _check_model(c, version)
        with st.model():
            try:
                res = promote(c, version, body.approver.strip(), note=body.note.strip())
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            st.release_models()
        return res

    return app


def serve(c: Container, host: str, port: int, reference: str | None, origins: list[str] | None) -> None:
    import uvicorn  # noqa: PLC0415

    uvicorn.run(create_app(c, reference, origins), host=host, port=port, log_level="info")
