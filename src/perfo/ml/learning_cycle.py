"""Continuous learning (HU16): corrections snapshot, retraining with replay, recalibration, gates G1–G6 and promotion.

The recognizer is never updated online. A cycle trains a candidate; the active model only changes through an
explicit promotion that requires the configured gates to pass.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import numpy as np
from PIL import Image

from perfo.application.preprocess import preprocess
from perfo.application.segment import segment
from perfo.domain.entities import AuditEvent
from perfo.domain.gates import GateResult, GateTargets, g1_integrity, g2_quality, g3_calibration, g4_homologation, g5_operation, g6_approval
from perfo.ml.dataset import LabeledCrop, LiteralLabeler, leakage_check, snapshot_manifest
from perfo.ml.groundtruth import GroundTruth
from perfo.ml.metrics import literal, percentile
from perfo.ml.pipeline import SPLIT_VERSION, calibrate, evaluate_models, evaluate_routing, finetune, latest_initial_snapshot, load_snapshot_items
from perfo.ml.train import TrainingFailed

INITIAL_REQUIRED = ("G1", "G2", "G6")
CYCLE_REQUIRED = ("G1", "G2", "G3", "G4", "G5", "G6")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def protected_docs(split: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k in ("test", "val_select", "val_calib", "sprint1_sample"):
        for d in split.get(k, []):
            out[d] = k
    return out


def corrections_to_examples(c: Any, split: dict[str, Any], include_positives: bool) -> tuple[list[LabeledCrop], Counter]:
    """Validated corrections -> labeled crops. Corrections from protected documents are excluded (leakage)."""
    labeler = LiteralLabeler(c.masters, c.abbreviations)
    spec_of = c.spec_of
    prot = protected_docs(split)
    max_d = float(c.cfg["dataset"].get("literal_max_distance", 0.5))
    excluded: Counter = Counter()
    out: list[LabeledCrop] = []
    kinds = ["correction", "confirmed_review"] + (["confirmed_auto"] if include_positives else [])
    for corr in c.repo.list_corrections(unused_only=False, kinds=kinds):
        if corr.document_id in prot:
            excluded[f"documento_protegido:{prot[corr.document_id]}"] += 1
            continue
        crop = c.repo.get_crop(corr.crop_id) if corr.crop_id else None
        if crop is None or crop.object_key is None or crop.mark is not None:
            excluded["sin_recorte_legible"] += 1
            continue
        if corr.validated_value is None:
            excluded["valor_vacio"] += 1
            continue
        spec = spec_of[corr.field_id]
        if corr.validated_text:
            text, dist = literal(corr.validated_text), 0.0
        elif corr.kind != "correction" and corr.recognized_text:
            text, dist = literal(corr.recognized_text), 0.0  # confirmed reading
        else:
            text, dist = labeler.label(spec.type.value, spec.key, corr.validated_value, spec.master_type, corr.recognized_text)
            if text is None or dist > max(max_d, 0.75):
                excluded["etiqueta_literal_no_determinable"] += 1
                continue
        src = {"correction": "correction", "confirmed_review": "confirmed", "confirmed_auto": "confirmed_auto"}[corr.kind]
        out.append(LabeledCrop(corr.document_id, crop.id, crop.sha256, crop.object_key, corr.field_id, spec.key, spec.type.value, text,
                               corr.validated_value, src, round(dist, 4), None, "train"))
    return out, excluded


def measure_latency(c: Any, version: str, doc_ids: list[str], max_docs: int = 5) -> dict[str, float]:
    """Per-format latency: preprocessing + segmentation + recognition of the readable crops (no persistence)."""
    rec = c.recognizer(version)
    times = []
    for did in doc_ids[:max_docs]:
        doc = c.repo.get_document(did)
        t0 = time.perf_counter()
        if doc.template_version != c.template.version:  # latency is measured on the main form
            continue
        pre = preprocess(c.storage.get(doc.original_key), c.template, c.reference_path, c.cfg.get_path("preprocessing", {}))
        if not pre.ok:
            continue
        seg = segment(pre.image, c.template, c.reference_path, c.cfg.get_path("segmentation", {}))
        items = [(ci.png, ci.spec.type.value) for ci in seg.crops if ci.png is not None]
        rec.recognize_batch(items)
        times.append(time.perf_counter() - t0)
    return {"n": len(times), "p50_s": percentile(times, 50), "p95_s": percentile(times, 95)}


def evaluate_gates(
    c: Any, candidate: str, active: str, gt: GroundTruth, snapshot_items: list[LabeledCrop], provenance_ok: bool,
    excluded_conflicts: int, log: Any = print,
) -> dict[str, Any]:
    split = c.repo.get_split(SPLIT_VERSION)
    th = c.cfg["thresholds"]
    targets = GateTargets(ece_max=float(th["ece_target"]), risk_max=float(th["risk_target"]), latency_p95_max_s=float(th["latency_p95_budget_s"]))
    doc_sha = split.get("doc_sha256", {})
    doc_sha.update({d.id: d.sha256 for d in c.repo.list_documents()})
    doc_key = {d.id: d.business_key for d in c.repo.list_documents() if d.business_key}
    leaks = leakage_check(split, [i for i in snapshot_items if i.split == "train"], doc_sha, doc_key)
    g1 = g1_integrity(provenance_ok, leaks, excluded_conflicts)

    ev = evaluate_models(c, [active, candidate], split, gt, log=log)
    ma, mc = ev["models"][active], ev["models"][candidate]
    g2 = g2_quality({"cer": ma["cer"], "field_accuracy": ma["field_accuracy"]}, {"cer": mc["cer"], "field_accuracy": mc["field_accuracy"]}, targets)
    rt = evaluate_routing(c, candidate, split["test"], gt, log=log)
    g3 = g3_calibration(rt["ece"], rt["residual_risk"], rt["coverage"], targets)
    g4 = g4_homologation({"acc_at_1": ma["homologation"]["acc_at_1"], "recall_at_3": ma["homologation"]["recall_at_3"]},
                         {"acc_at_1": mc["homologation"]["acc_at_1"], "recall_at_3": mc["homologation"]["recall_at_3"]})
    lat = measure_latency(c, candidate, split["test"])
    info = c.registry.get(candidate)
    reproducible = bool(info.artifact_sha256) and bool(info.metadata.get("seed") is not None) and bool(info.metadata.get("dataset_snapshot"))
    g5 = g5_operation(lat["p95_s"], reproducible, info.artifact_sha256, targets)
    g6 = g6_approval(None, None, candidate, None)
    gates = [g1, g2, g3, g4, g5, g6]
    ie = 100.0 * (ma["cer"] - mc["cer"]) / ma["cer"] if ma["cer"] else float("nan")
    out = {"candidate": candidate, "active": active, "gates": [asdict(g) for g in gates], "evaluation": ev, "routing": rt, "latency": lat,
           "relative_cer_reduction_pct": ie, "evaluated_at": _now()}
    c.registry.attach(candidate, "gates", out)
    for g in gates:
        log(f"    {g.gate} {g.name:<13} {'✔' if g.passed else '✘'}  {g.detail}")
    return out


def run_cycle(c: Any, gt: GroundTruth, log: Any = print) -> dict[str, Any]:
    split = c.repo.get_split(SPLIT_VERSION)
    active = c.registry.get_active()
    if active is None:
        raise RuntimeError("No hay modelo activo")
    cfg = c.cfg["cycle"]
    seed = int(c.cfg["seed"])
    new, excluded = corrections_to_examples(c, split, bool(cfg.get("include_confirmed_positives", True)))
    corrections = [i for i in new if i.source == "correction"]
    confirmed = [i for i in new if i.source != "correction"]
    # prioritise corrections; confirmed positives are low priority (subsampled to the number of corrections)
    rng = random.Random(seed)
    rng.shuffle(confirmed)
    priority = corrections * max(1, int(cfg.get("correction_weight", 1))) + confirmed[: max(len(corrections), 1)]
    snap_items_initial = load_snapshot_items(c, latest_initial_snapshot(c))
    hist_train = [i for i in snap_items_initial if i.split == "train"]
    n_replay = min(len(hist_train), int(round(float(cfg.get("replay_ratio", 2.0)) * len(priority))))
    replay = rng.sample(hist_train, k=n_replay)
    for i in replay:
        i.source = "replay"
    items = priority + replay + [i for i in snap_items_initial if i.split == "val_select"]
    provenance_ok = all(i.sha256 and i.object_key and c.storage.exists(i.object_key) for i in priority + replay)
    n_prev = len([s for s in c.repo.list_snapshots() if s["kind"] == "cycle"])
    snap_id = f"ds-cycle-{n_prev + 1:02d}-{hashlib.sha256(json.dumps(sorted(i.crop_id for i in priority + replay)).encode()).hexdigest()[:8]}"
    n_conf_used = min(len(confirmed), max(len(corrections), 1))
    stats = {"corrections": len(corrections), "correction_weight": int(cfg.get("correction_weight", 1)), "confirmed_positives_used": n_conf_used, "confirmed_available": len(confirmed),
             "replay": len(replay), "excluded": dict(excluded), "parent": active.version, "provenance_complete": provenance_ok}
    data, sha = snapshot_manifest(snap_id, "cycle", items, {"split_version": SPLIT_VERSION, "stats": stats})
    key = f"datasets/{snap_id}.json"
    c.storage.put(key, data, {"kind": "cycle"})
    c.repo.save_snapshot({"id": snap_id, "kind": "cycle", "manifest_key": key, "manifest_sha256": sha, "counts": stats})
    log(f"  Snapshot {snap_id}: {len(corrections)} correcciones (×{stats['correction_weight']}) + {n_conf_used} confirmados + {len(replay)} de replay; excluidos {dict(excluded)}")

    result: dict[str, Any] = {"snapshot": snap_id, "stats": stats, "active_before": active.version, "candidate": None, "promoted": False}
    if not priority:
        result["status"] = "sin_correcciones"
        log("  No hay correcciones nuevas; el ciclo no reentrena")
        return result
    # G1 is checked before spending compute
    all_docs = c.repo.list_documents()
    leaks = leakage_check(split, priority + replay, {d.id: d.sha256 for d in all_docs}, {d.id: d.business_key for d in all_docs if d.business_key})
    if leaks or not provenance_ok:
        result["status"] = "bloqueado_g1"
        result["gates"] = [asdict(g1_integrity(provenance_ok, leaks, sum(excluded.values())))]
        log(f"  G1 falló: {leaks[:3]}; el modelo activo no cambia")
        return result
    version = f"cycle{n_prev + 1:02d}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    train_cfg = {**c.cfg["training"], **{k: cfg[k] for k in ("max_steps", "eval_every", "patience", "lr", "warmup_ratio") if k in cfg}}
    try:
        cand = finetune(c, items, snap_id, parent=active.version, version=version, train_cfg=train_cfg, log=log)
    except TrainingFailed as exc:
        result["status"] = "entrenamiento_fallido"
        result["error"] = str(exc)
        log(f"  Reentrenamiento fallido ({exc}); el modelo activo {active.version} se mantiene")
        return result
    result["candidate"] = cand.version
    log("  Recalibración del candidato:")
    calibrate(c, cand.version, split, snap_items_initial, log=log)
    log("  Compuertas:")
    gates = evaluate_gates(c, cand.version, active.version, gt, priority + replay, provenance_ok, sum(excluded.values()), log=log)
    result.update(gates=gates["gates"], ie5_pct=gates["relative_cer_reduction_pct"], evaluation=gates["evaluation"], routing=gates["routing"],
                  latency=gates["latency"], status="pendiente_aprobacion")
    c.registry.attach(cand.version, "cycle", {k: v for k, v in result.items() if k not in ("evaluation", "routing")})
    return result


def promote(c: Any, version: str, approver: str, required: tuple[str, ...] | None = None, note: str = "") -> dict[str, Any]:
    """G6: explicit promotion. Refused when any required gate (G1–G5) failed; the active model then stays."""
    info = c.registry.get(version)
    gates = c.registry.read_attachment(version, "gates")
    if gates is None:
        raise ValueError(f"El candidato {version} no tiene evaluación de compuertas; ejecute el ciclo o 'perfo gates'")
    required = required or (CYCLE_REQUIRED if info.parent and not str(info.parent).startswith("baseline-") else INITIAL_REQUIRED)
    by_gate = {g["gate"]: g for g in gates["gates"]}
    failed = [g for g in required if g != "G6" and not by_gate.get(g, {}).get("passed")]
    at = _now()
    evidence = c.registry.attach(version, "promotion_request", {"approver": approver, "at": at, "required": list(required), "failed": failed, "note": note})
    if failed:
        c.registry.set_status(version, "rejected", "compuertas fallidas: " + ", ".join(failed))
        c.repo.upsert_model_version(c.registry.get(version).__dict__)
        c.repo.add_audit(AuditEvent("model_promotion_refused", "model", version, approver, after={"failed": failed}))
        return {"promoted": False, "failed": failed, "active": c.registry.get_active().version, "evidence": evidence}
    g6 = g6_approval(approver, at, version, evidence)
    gates["gates"] = [g for g in gates["gates"] if g["gate"] != "G6"] + [asdict(g6)]
    c.registry.attach(version, "gates", gates)
    previous = c.registry.get_active()
    c.registry.set_active(version, {"approver": approver, "evidence": evidence, "required_gates": list(required), "note": note})
    for v in (version, previous.version if previous else None):
        if v:
            c.repo.upsert_model_version(c.registry.get(v).__dict__)
    c.repo.add_audit(AuditEvent("model_promoted", "model", version, approver, before={"active": previous.version if previous else None}, after={"active": version, "evidence": evidence}))
    return {"promoted": True, "failed": [], "active": version, "previous": previous.version if previous else None, "evidence": evidence, "g6": asdict(g6)}


def gate_summary(gates: list[dict[str, Any]]) -> str:
    return " ".join(f"{g['gate']}{'✔' if g['passed'] else '✘'}" for g in gates)


def load_image(storage: Any, key: str) -> Image.Image:
    return Image.open(io.BytesIO(storage.get(key))).convert("RGB")


def np_mean(xs: list[float]) -> float:
    return float(np.mean(xs)) if xs else float("nan")


__all__ = ["CYCLE_REQUIRED", "INITIAL_REQUIRED", "GateResult", "evaluate_gates", "promote", "run_cycle"]
