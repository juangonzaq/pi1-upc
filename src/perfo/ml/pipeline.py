"""ML use cases: build dataset, fine-tune, evaluate, calibrate and evaluate routing."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from perfo.domain.normalization import compact
from perfo.domain.routing import RoutingPolicy
from perfo.ml.dataset import LabeledCrop, LeakageError, build_weak_dataset, leakage_check, snapshot_manifest, split_documents
from perfo.ml.evaluate import (
    CropItem,
    calibration_summary,
    crop_items,
    field_accuracy,
    homologation_cases,
    homologation_eval,
    infer_items,
    route_items,
    summarize_text,
    text_rows,
)
from perfo.ml.groundtruth import GroundTruth
from perfo.ml.metrics import choose_threshold, coverage_risk, literal, nll_binary
from perfo.ml.train import TrainSample, train_trocr

SPLIT_VERSION = "split-v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def baseline_version(c: Any) -> str:
    for info in c.registry.list():
        if info.status in ("baseline", "active", "retired") and info.parent is None and info.version.startswith("baseline-"):
            return info.version
    return c.ensure_baseline().version


# ------------------------------------------------------------------ dataset (HU02)


def build_dataset(c: Any, history_csv: str | Path, log: Any = print) -> dict[str, Any]:
    cfg = c.cfg["dataset"]
    base = baseline_version(c)
    built = build_weak_dataset(c, history_csv, base, float(cfg.get("literal_max_distance", 0.5)))
    doc_sha = {d.id: d.sha256 for d in c.repo.list_documents()}
    existing = c.repo.get_split(SPLIT_VERSION)
    frozen_test = existing["test"] if existing and existing.get("frozen") else None
    if existing and existing.get("frozen"):
        split = {k: existing[k] for k in ("train", "val_select", "val_calib", "test", "sprint1_sample", "reference_subset", "unseen_operators", "ratios", "seed")}
        new_docs = [d for d in built.aligned_docs if d not in set().union(*[set(split[k]) for k in ("train", "val_select", "val_calib", "test", "sprint1_sample")])]
        split["train"] = sorted(split["train"] + new_docs)
    else:
        split = split_documents(
            built.aligned_docs, built.doc_operator, int(c.cfg["seed"]), tuple(cfg.get("split", [0.7, 0.15, 0.15])),  # type: ignore[arg-type]
            int(cfg.get("unseen_operators", 0)), int(cfg.get("sprint1_sample", 0)), int(cfg.get("reference_subset", 0)), frozen_test,
        )
    where = {d: k for k in ("train", "val_select", "val_calib", "test", "sprint1_sample") for d in split[k]}
    for it in built.items:
        it.split = where.get(it.doc_id)
    train_items = [i for i in built.items if i.split == "train"]
    leaks = leakage_check(split, train_items, doc_sha, built.doc_key)
    if leaks:
        raise LeakageError("Fuga de datos detectada: " + "; ".join(leaks[:5]))
    manifest = {**split, "doc_sha256": {d: doc_sha[d] for d in where}, "doc_operator": built.doc_operator, "doc_key": built.doc_key}
    if not existing:
        c.repo.save_split(SPLIT_VERSION, manifest, frozen=True)

    required = int(cfg.get("required_formats", 0)) or len(built.aligned_docs)
    ie2 = 100.0 * len(built.aligned_docs) / required if required else 0.0
    by_split = Counter(i.split for i in built.items)
    by_type = Counter(i.field_type for i in built.items)
    stats = {
        "aligned_formats": len(built.aligned_docs),
        "excluded_formats": len(built.excluded_docs),
        "excluded_by_cause": dict(Counter(built.excluded_docs.values())),
        "excluded_crops": dict(built.excluded_crops),
        "labeled_crops": len(built.items),
        "by_split_formats": {k: len(split[k]) for k in ("train", "val_select", "val_calib", "test", "sprint1_sample")},
        "by_split_crops": dict(by_split),
        "by_type_crops": dict(by_type),
        "operators": dict(Counter(built.doc_operator.values())),
        "unseen_operators": split["unseen_operators"],
        "label_distance_mean": float(np.mean([i.distance for i in built.items])) if built.items else 0.0,
        "required_formats": required,
        "IE2_pct": round(ie2, 1),
        "leakage": leaks,
    }
    snap_id = f"ds-initial-{hashlib.sha256(json.dumps(sorted(i.crop_id for i in built.items)).encode()).hexdigest()[:10]}"
    data, sha = snapshot_manifest(snap_id, "initial", built.items, {"split_version": SPLIT_VERSION, "baseline": base, "stats": stats})
    key = f"datasets/{snap_id}.json"
    if not c.storage.exists(key):
        c.storage.put(key, data, {"kind": "initial"})
        c.repo.save_snapshot({"id": snap_id, "kind": "initial", "manifest_key": key, "manifest_sha256": sha, "counts": stats})
    rep_dir = c.workspace / "reports"
    rep_dir.mkdir(exist_ok=True)
    (rep_dir / f"dataset_{snap_id}.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    log(f"  Dataset {snap_id}: {stats['aligned_formats']} formatos alineados, {stats['excluded_formats']} excluidos {stats['excluded_by_cause']}")
    log(f"  Recortes etiquetados {stats['labeled_crops']} · excluidos {stats['excluded_crops']} · IE2 = {stats['IE2_pct']} %")
    return {"snapshot_id": snap_id, "split": split, "items": built.items, "stats": stats, "doc_key": built.doc_key}


def load_snapshot_items(c: Any, snapshot_id: str) -> list[LabeledCrop]:
    snaps = {s["id"]: s for s in c.repo.list_snapshots()}
    data = json.loads(c.storage.get(snaps[snapshot_id]["manifest_key"]))
    return [LabeledCrop(**i) for i in data["items"]]


def latest_initial_snapshot(c: Any) -> str | None:
    snaps = [s for s in c.repo.list_snapshots() if s["kind"] == "initial"]
    return snaps[-1]["id"] if snaps else None


# ------------------------------------------------------------------ fine-tuning (HU14)


def finetune(c: Any, items: list[LabeledCrop], snapshot_id: str, parent: str | None = None, version: str | None = None,
             train_cfg: dict[str, Any] | None = None, extra_train: list[LabeledCrop] | None = None, log: Any = print) -> Any:
    cfg = dict(train_cfg or c.cfg["training"])
    parent_v = parent or baseline_version(c)
    parent_info = c.registry.get(parent_v)
    train = [TrainSample(i.object_key, i.label_text) for i in items if i.split == "train"]
    train += [TrainSample(i.object_key, i.label_text) for i in (extra_train or [])]
    select = [TrainSample(i.object_key, i.label_text) for i in items if i.split == "val_select"]
    version = version or f"ft-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    out_dir = c.workspace / "artifacts" / version
    log(f"  Ajuste fino {version} desde {parent_v}: {len(train)} ejemplos de entrenamiento, {len(select)} de selección, dispositivo {c.device()}")
    res = train_trocr(c.storage, parent_info.artifact_uri, train, select, out_dir, cfg, c.device(), int(c.cfg["seed"]), log=log)
    info = c.registry.publish(version, str(out_dir), {
        "base_checkpoint": parent_info.base_checkpoint, "parent": parent_v, "status": "candidate", "dataset_snapshot": snapshot_id,
        "seed": int(c.cfg["seed"]), "training": asdict(res) | {"history": res.history},
    })
    c.repo.upsert_model_version(info.__dict__)
    log(f"  Entrenado en {res.seconds:.0f} s · mejor paso {res.best_step} · CER selección {res.initial_select_cer:.4f} → {res.best_select_cer:.4f}")
    return info


# ------------------------------------------------------------------ evaluation (HU14)


def _texts_for(c: Any, version: str, items: list[CropItem], temps: list[float] | None = None) -> tuple[list[str | None], list[Any]]:
    """Inference with an in-process cache: (model, crop) -> TokenStats over the full temperature grid."""
    grid = sorted({1.0, *[float(t) for t in c.cfg["calibration"]["temperatures"]], *(temps or [])})
    cache = c.__dict__.setdefault("_infer_cache", {})
    missing = [it for it in items if (version, it.crop_id) not in cache and it.object_key and it.mark is None]
    if missing:
        rec = c.recognizer(version, calibrated=False)
        for it, st in zip(missing, infer_items(rec, c.storage, missing, grid), strict=True):
            cache[(version, it.crop_id)] = st
    stats = [cache.get((version, it.crop_id)) for it in items]
    return [s.text if s is not None else None for s in stats], stats


def evaluate_models(c: Any, versions: list[str], split: dict[str, Any], gt: GroundTruth, log: Any = print) -> dict[str, Any]:
    items = crop_items(c, split["test"], gt)
    unseen = set(split.get("unseen_operators", []))
    reference = set(split.get("reference_subset", []))
    results: dict[str, Any] = {"n_formats": len(split["test"]), "n_crops": len(items), "models": {}}
    for v in versions:
        t0 = time.perf_counter()
        stored = {r.crop_id: r.text for r in c.repo.get_recognitions([i.crop_id for i in items], v)}
        readable = [i for i in items if i.object_key and i.mark is None]
        if readable and all(i.crop_id in stored for i in readable):
            texts = [stored.get(i.crop_id) if i.mark is None else None for i in items]  # same greedy decoding, already persisted
        else:
            texts, _ = _texts_for(c, v, items)
        rows = text_rows(items, texts)
        summ = summarize_text(rows)
        fa = field_accuracy(c, items, texts, gt)
        unseen_rows = [r for r in rows if r["operator"] in unseen]
        ref_rows = [r for r in rows if r["doc_id"] in reference]
        hom = homologation_eval(c.homologator, homologation_cases(c, items, texts))
        results["models"][v] = {
            "cer": summ["cer"], "wer": summ["wer"], "exact": summ["exact"], "by_type": summ["by_type"], "by_field": summ["by_field"],
            "field_accuracy": fa["field_accuracy"], "field_accuracy_by_type": fa["by_type"],
            "cer_unseen_operators": summarize_text(unseen_rows)["cer"] if unseen_rows else None, "n_unseen": len(unseen_rows),
            "cer_reference_subset": summarize_text(ref_rows)["cer"] if ref_rows else None,
            "homologation": hom, "seconds": time.perf_counter() - t0, "n": summ["n"],
        }
        log(f"  {v}: CER {summ['cer']:.4f} · WER {summ['wer']:.4f} · exactitud por campo {fa['field_accuracy']:.3f} · "
            f"CER operadores no vistos {results['models'][v]['cer_unseen_operators'] if unseen_rows else float('nan'):.4f}")
    if len(versions) >= 2:
        a, b = results["models"][versions[0]]["cer"], results["models"][versions[1]]["cer"]
        results["relative_cer_reduction_pct"] = 100.0 * (a - b) / a if a else float("nan")
    return results


# ------------------------------------------------------------------ calibration and routing (HU15)


def _labels_from_items(items: list[CropItem], labeled: dict[str, LabeledCrop]) -> list[CropItem]:
    for it in items:
        lab = labeled.get(it.crop_id)
        it.ref_text = lab.label_text if lab else None
        it.ref_value = lab.label_value if lab else None
        it.label_source = lab.source if lab else "none"
    return items


def calibrate(c: Any, version: str, split: dict[str, Any], labeled_items: list[LabeledCrop], log: Any = print) -> dict[str, Any]:
    """Per-field-type temperature scaling + threshold on the coverage–risk curve (calibration half)."""
    th = c.cfg["thresholds"]
    temps = [float(t) for t in c.cfg["calibration"]["temperatures"]]
    agg = c.cfg["model"].get("aggregation", "mean_logprob")
    bins = int(c.cfg["calibration"].get("ece_bins", 15))
    labeled = {i.crop_id: i for i in labeled_items}
    items = _labels_from_items(crop_items(c, split["val_calib"], None), labeled)
    texts, stats = _texts_for(c, version, items, temps)

    target = c.cfg["calibration"].get("target", "value")
    truth = {(it.doc_id, it.field_id): it.ref_value for it in items}
    value_ok: dict[tuple[str, str], bool] = {}
    if target == "value":
        raw = [st.conf[agg][1.0] if st is not None else None for st in stats]
        for r in route_items(c, items, texts, raw, [st is not None for st in stats], RoutingPolicy(thresholds={}), truth):
            if r["truth"] is not None:
                value_ok[(r["doc_id"], r["field_id"])] = bool(r["correct"])
    by_type: dict[str, list[int]] = defaultdict(list)
    for i, (it, st) in enumerate(zip(items, stats, strict=True)):
        has_label = (it.doc_id, it.field_id) in value_ok if target == "value" else it.ref_text is not None
        if st is not None and has_label:
            by_type[it.field_type].append(i)
    temperatures: dict[str, float | None] = {}
    details: dict[str, Any] = {}
    min_n = int(c.cfg["calibration"].get("min_samples", 20))
    for ftype in sorted({it.field_type for it in items}):
        idx = by_type.get(ftype, [])
        if target == "value":
            correct = np.array([value_ok[(items[i].doc_id, items[i].field_id)] for i in idx], float)
        else:
            correct = np.array([compact(texts[i] or "") == compact(items[i].ref_text or "") for i in idx], float)
        if len(idx) < min_n:
            temperatures[ftype] = None
            details[ftype] = {"n": len(idx), "reason": "muestras_insuficientes"}
            continue
        nll = {T: nll_binary(np.array([stats[i].conf[agg][T] for i in idx]), correct) for T in temps}
        best_T = min(nll, key=lambda T: (nll[T], abs(T - 1.0)))
        conf_raw = np.array([stats[i].conf[agg][1.0] for i in idx])
        conf_cal = np.array([stats[i].conf[agg][best_T] for i in idx])
        temperatures[ftype] = best_T
        details[ftype] = {"n": len(idx), "T": best_T, "nll_T1": nll[1.0], "nll_best": nll[best_T],
                          "ece_raw": calibration_summary(conf_raw, correct, bins)["ece"], "ece_cal": calibration_summary(conf_cal, correct, bins)["ece"],
                          "accuracy": float(correct.mean())}

    # thresholds on the coverage–risk curve, with the forcing gates applied
    confs, reliable = [], []
    for it, st in zip(items, stats, strict=True):
        T = temperatures.get(it.field_type)
        if st is None or T is None:
            confs.append(st.conf[agg][1.0] if st is not None else None)
            reliable.append(False)
        else:
            confs.append(st.conf[agg][T])
            reliable.append(bool(st.text))
    records = route_items(c, items, texts, confs, reliable, RoutingPolicy(thresholds={}), truth)
    labeled_records = [r for r in records if r["truth"] is not None and r["conf"] is not None and r["reliable"]]
    thresholds: dict[str, float | None] = {}
    thr_details: dict[str, Any] = {}
    for ftype in sorted({r["type"] for r in records}):
        rs = [r for r in labeled_records if r["type"] == ftype]
        ch = choose_threshold(np.array([r["conf"] for r in rs]), np.array([r["correct"] for r in rs]), np.array([r["gate_ok"] for r in rs]),
                              float(th["risk_target"]), int(th["min_support"]))
        if temperatures.get(ftype) is None:
            ch = {"threshold": None, "coverage": 0.0, "risk": None, "accepted": 0, "reason": "sin_temperatura", "n": details.get(ftype, {}).get("n", 0)}
        thresholds[ftype] = ch["threshold"]
        thr_details[ftype] = ch
    profile = {
        "id": f"cal-{version}-{hashlib.sha256(json.dumps(temperatures, sort_keys=True).encode()).hexdigest()[:8]}",
        "model_version": version, "aggregation": agg, "temperatures": temperatures, "thresholds": thresholds,
        "temperature_details": details, "threshold_details": thr_details, "risk_target": float(th["risk_target"]),
        "min_support": int(th["min_support"]), "calibration_formats": len(split["val_calib"]), "target": target, "created_at": _now(),
    }
    c.registry.attach(version, "calibration", profile)
    c.repo.add_calibration_profile(profile)
    for k in list(c._recognizers):
        if k.startswith(f"{version}|"):
            del c._recognizers[k]
    for ftype in sorted(thresholds):
        d = details.get(ftype, {})
        log(f"    {ftype:<11} T={temperatures.get(ftype)}  umbral={thresholds[ftype]}  n={d.get('n')}  ({thr_details[ftype].get('reason')})")
    return profile


def evaluate_routing(c: Any, version: str, docs: list[str], gt: GroundTruth, log: Any = print) -> dict[str, Any]:
    """ECE (15 bins), reliability, coverage–risk and IE4 on a labeled document set (frozen test by default)."""
    prof = c.calibration_profile(version) or {}
    agg = prof.get("aggregation", c.cfg["model"].get("aggregation", "mean_logprob"))
    bins = int(c.cfg["calibration"].get("ece_bins", 15))
    temps_by_type = prof.get("temperatures", {})
    temps = sorted({float(t) for t in temps_by_type.values() if t is not None})
    items = crop_items(c, docs, gt)
    texts, stats = _texts_for(c, version, items, temps)
    confs, raw, reliable = [], [], []
    for it, st in zip(items, stats, strict=True):
        T = temps_by_type.get(it.field_type)
        if st is None:
            confs.append(None), raw.append(None), reliable.append(False)
            continue
        raw.append(st.conf[agg][1.0])
        confs.append(st.conf[agg][float(T)] if T is not None else st.conf[agg][1.0])
        reliable.append(T is not None and bool(st.text))
    # calibration quality on literal text correctness (informative)
    idx = [i for i, it in enumerate(items) if stats[i] is not None and it.ref_text is not None]
    correct_text = np.array([compact(texts[i] or "") == compact(items[i].ref_text or "") for i in idx], float)
    cal_text = calibration_summary(np.array([confs[i] for i in idx]), correct_text, bins)
    # routing
    truth = {(it.doc_id, it.field_id): gt.value(it.form_id, it.field_id) for it in items}
    for d in docs:
        doc = c.repo.get_document(d)
        fid = doc.metadata.get("form_id") if doc else None
        for spec in c.template.prioritized():
            if spec.required == "always":
                truth.setdefault((d, spec.id), gt.value(fid, spec.id) if fid else None)
    policy = c.routing_policy(version)
    records = route_items(c, items, texts, confs, reliable, policy, truth)
    # calibration quality on value correctness (what routing decides on)
    raw_by_key = {(it.doc_id, it.field_id): raw[i] for i, it in enumerate(items)}
    cr = [r for r in records if r["conf"] is not None and r["truth"] is not None]
    corr_v = np.array([r["correct"] for r in cr], float)
    cal = calibration_summary(np.array([r["conf"] for r in cr]), corr_v, bins)
    unc = calibration_summary(np.array([raw_by_key.get((r["doc_id"], r["field_id"])) or r["conf"] for r in cr]), corr_v, bins)
    ece_by_type = {}
    for ftype in sorted({r["type"] for r in cr}):
        sel = [k for k, r in enumerate(cr) if r["type"] == ftype]
        ece_by_type[ftype] = calibration_summary(np.array([cr[k]["conf"] for k in sel]), corr_v[sel], bins)["ece"]
    auto = [r for r in records if r["route"] == "AUTO"]
    n = len(records)
    coverage = len(auto) / n if n else 0.0
    risk = float(np.mean([not r["correct"] for r in auto])) if auto else 0.0
    by_type = {}
    for ftype in sorted({r["type"] for r in records}):
        rs = [r for r in records if r["type"] == ftype]
        au = [r for r in rs if r["route"] == "AUTO"]
        by_type[ftype] = {"n": len(rs), "auto": len(au), "coverage": len(au) / len(rs), "risk": float(np.mean([not r["correct"] for r in au])) if au else 0.0,
                          "threshold": policy.thresholds.get(ftype)}
    reasons = Counter(r.split(":")[0] for rec in records for r in rec["reasons"])
    curve = {}
    for ftype in sorted({r["type"] for r in records}):
        rs = [r for r in records if r["type"] == ftype and r["conf"] is not None]
        pts = coverage_risk(np.array([r["conf"] for r in rs]), np.array([r["correct"] for r in rs]), np.array([r["gate_ok"] for r in rs]))
        curve[ftype] = pts[:: max(1, len(pts) // 60)]
    overall_pts = coverage_risk(np.array([r["conf"] or 0 for r in records]), np.array([r["correct"] for r in records]),
                                np.array([r["gate_ok"] and r["reliable"] for r in records]))
    out = {
        "n_fields": n, "auto": len(auto), "coverage": coverage, "residual_risk": risk, "by_type": by_type, "review_reasons": dict(reasons),
        "ece": cal["ece"], "ece_uncalibrated": unc["ece"], "ece_by_type": ece_by_type, "reliability": cal["reliability"],
        "reliability_uncalibrated": unc["reliability"], "value_accuracy": cal["accuracy"], "ece_text": cal_text["ece"],
        "text_accuracy": cal_text["accuracy"], "ece_target_kind": "valor", "curve": curve,
        "curve_overall": overall_pts[:: max(1, len(overall_pts) // 80)], "IE4_pct": 100 * coverage,
        "records_value_accuracy": float(np.mean([r["correct"] for r in records if r["truth"] is not None])) if records else float("nan"),
    }
    log(f"  Enrutamiento {version}: cobertura {coverage:.3f} ({len(auto)}/{n}) · error residual {risk:.4f} · ECE {cal['ece']:.4f} (sin calibrar {unc['ece']:.4f})")
    return out


def literal_of(text: str | None) -> str:
    return literal(text)
