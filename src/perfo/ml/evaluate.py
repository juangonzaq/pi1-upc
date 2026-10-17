"""Evaluation helpers: crop items, batched inference, CER by field type, field accuracy, homologation and routing."""

from __future__ import annotations

import io
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from perfo.application.homologation import Homologator
from perfo.application.structuring import FieldInput, effective_code_sets, structure
from perfo.domain.fields import FieldType, Template
from perfo.domain.normalization import compact
from perfo.domain.parsing import parse_field
from perfo.domain.routing import RoutingPolicy
from perfo.domain.rules import RuleConfig
from perfo.ml.groundtruth import GroundTruth
from perfo.ml.metrics import cer, ece, grouped_cer, literal, percentile, reliability, wer


@dataclass
class CropItem:
    doc_id: str
    form_id: str
    crop_id: str
    field_id: str
    key: str
    field_type: str
    object_key: str | None
    mark: str | None
    is_empty: bool
    operator: str | None
    ref_text: str | None = None  # literal label (uppercased)
    ref_value: str | None = None  # canonical value label
    label_source: str = "gt"
    extra: dict[str, Any] = field(default_factory=dict)


def crop_items(c: Any, doc_ids: list[str], gt: GroundTruth | None) -> list[CropItem]:
    """All segmented field crops of the given documents (non-empty and marks), with GT labels when available."""
    spec_of = {f.id: f for f in c.template.fields}
    out: list[CropItem] = []
    for did in doc_ids:
        doc = c.repo.get_document(did)
        form_id = doc.metadata.get("form_id", "") if doc else ""
        operator = gt.form(form_id)["operator"] if gt and form_id in gt._forms else None
        for cr in c.repo.get_crops(did):
            if cr.is_empty:
                continue
            spec = spec_of[cr.field_id]
            item = CropItem(did, form_id, cr.id, cr.field_id, spec.key, spec.type.value, cr.object_key, cr.mark, cr.is_empty, operator)
            if gt is not None:
                item.ref_text = gt.literal(form_id, cr.field_id)
                item.ref_value = gt.value(form_id, cr.field_id)
            out.append(item)
    return out


def load_images(storage: Any, items: list[CropItem]) -> list[Image.Image]:
    return [Image.open(io.BytesIO(storage.get(it.object_key))).convert("RGB") for it in items]  # type: ignore[arg-type]


def infer_items(recognizer: Any, storage: Any, items: list[CropItem], temperatures: list[float] | None = None, chunk: int = 512) -> list[Any]:
    """Runs the recognizer on readable crops (marks excluded); returns TokenStats aligned with items (None for marks)."""
    readable = [i for i, it in enumerate(items) if it.object_key and it.mark is None]
    stats: list[Any] = [None] * len(items)
    for s in range(0, len(readable), chunk):
        idx = readable[s : s + chunk]
        res = recognizer.infer(load_images(storage, [items[i] for i in idx]), temperatures)
        for i, r in zip(idx, res, strict=True):
            stats[i] = r
    return stats


def text_rows(items: list[CropItem], texts: list[str | None]) -> list[dict[str, Any]]:
    rows = []
    for it, t in zip(items, texts, strict=True):
        if it.ref_text is None or it.mark is not None:
            continue
        rows.append({"doc_id": it.doc_id, "form_id": it.form_id, "field_id": it.field_id, "key": it.key, "type": it.field_type, "operator": it.operator,
                     "ref": it.ref_text, "hyp": literal(t)})
    return rows


def summarize_text(rows: list[dict[str, Any]]) -> dict[str, Any]:
    refs, hyps = [r["ref"] for r in rows], [r["hyp"] for r in rows]
    return {
        "n": len(rows),
        "cer": cer(refs, hyps),
        "wer": wer(refs, hyps),
        "exact": float(np.mean([compact(r) == compact(h) for r, h in zip(refs, hyps, strict=True)])) if rows else float("nan"),
        "by_type": grouped_cer(rows, "type"),
        "by_field": grouped_cer(rows, "key"),
    }


def predicted_value(template: Template, spec_id: str, text: str, shift: str | None, code_sets: dict, homologator: Homologator | None) -> str | None:
    spec = template.field(spec_id)
    if spec.type == FieldType.MASTER_REF:
        if homologator is None or not spec.master_type:
            return None
        return homologator.homologate(spec.master_type, text).code
    p = parse_field(spec, text, shift, code_sets)
    return p.value


def field_accuracy(c: Any, items: list[CropItem], texts: list[str | None], gt: GroundTruth) -> dict[str, Any]:
    """Value-level accuracy of the recognizer per field type (true shift context; homologation for master_ref)."""
    codes = c.masters.get_master("codigos").items
    code_sets = effective_code_sets(c.template, codes)
    by_type: dict[str, list[bool]] = defaultdict(list)
    for it, t in zip(items, texts, strict=True):
        if it.ref_value is None or it.mark is not None or t is None:
            continue
        shift = gt.value(it.form_id, "header.shift")
        pv = predicted_value(c.template, it.field_id, t, shift, code_sets, c.homologator)
        by_type[it.field_type].append(pv == it.ref_value)
    allv = [x for v in by_type.values() for x in v]
    return {"field_accuracy": float(np.mean(allv)) if allv else float("nan"), "n": len(allv),
            "by_type": {k: {"accuracy": float(np.mean(v)), "n": len(v)} for k, v in sorted(by_type.items())}}


def homologation_eval(homologator: Homologator, cases: list[tuple[str, str, str]]) -> dict[str, Any]:
    """cases: (master_type, input text, expected code) -> Exactitud@1, Recall@3 and auto-resolution."""
    if not cases:
        return {"n": 0, "acc_at_1": float("nan"), "recall_at_3": float("nan"), "auto_resolved": float("nan"), "by_type": {}}
    hits1, hits3, auto, auto_ok = [], [], [], []
    by_type: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for mtype, text, code in cases:
        h = homologator.homologate(mtype, text)
        top3 = [x["code"] for x in h.candidates[:3]]
        h1, h3 = h.code == code, code in top3
        hits1.append(h1)
        hits3.append(h3)
        auto.append(h.accepted)
        if h.accepted:
            auto_ok.append(h1)
        by_type[mtype].append((h1, h3))
    return {
        "n": len(cases),
        "acc_at_1": float(np.mean(hits1)),
        "recall_at_3": float(np.mean(hits3)),
        "auto_resolved": float(np.mean(auto)),
        "auto_precision": float(np.mean(auto_ok)) if auto_ok else float("nan"),
        "by_type": {k: {"acc_at_1": float(np.mean([a for a, _ in v])), "recall_at_3": float(np.mean([b for _, b in v])), "n": len(v)} for k, v in by_type.items()},
    }


def homologation_cases(c: Any, items: list[CropItem], texts: list[str | None] | None = None) -> list[tuple[str, str, str]]:
    """Homologation cases from master_ref crops: literal GT text (texts=None) or recognized text."""
    spec_of = {f.id: f for f in c.template.fields}
    cases = []
    for i, it in enumerate(items):
        spec = spec_of[it.field_id]
        if spec.type != FieldType.MASTER_REF or it.ref_value is None or it.mark is not None:
            continue
        text = it.ref_text if texts is None else texts[i]
        if text:
            cases.append((spec.master_type, text, it.ref_value))  # type: ignore[arg-type]
    return cases


def form_times(telemetry_path: str, model: str | None = None) -> dict[str, float]:
    vals = []
    try:
        with open(telemetry_path, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("kind") == "metric" and rec.get("name") == "tiempo_por_formato_s":
                    if model is None or rec.get("tags", {}).get("model") == model:
                        vals.append(float(rec["value"]))
    except FileNotFoundError:
        pass
    return {"n": len(vals), "p50_s": percentile(vals, 50), "p95_s": percentile(vals, 95)}


# ---------------------------------------------------------------- routing evaluation


def route_items(
    c: Any,
    items: list[CropItem],
    texts: list[str | None],
    confs: list[float | None],
    reliable: list[bool],
    policy: RoutingPolicy,
    gt_values: dict[tuple[str, str], str | None],
) -> list[dict[str, Any]]:
    """Document-level structuring + routing; returns one record per routed field with correctness against labels."""
    codes = c.masters.get_master("codigos").items
    code_sets = effective_code_sets(c.template, codes)
    rule_cfg = RuleConfig(**(c.cfg.get_path("rules", {}) or {}))
    spec_of = {f.id: f for f in c.template.fields}
    by_doc: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(items):
        by_doc[it.doc_id].append(i)
    records = []
    for did, idx in by_doc.items():
        inputs = []
        for i in idx:
            it = items[i]
            inputs.append(FieldInput(spec_of[it.field_id], it.crop_id, texts[i], confs[i], reliable[i], None, it.mark, False))
        present_ids = {items[i].field_id for i in idx}
        # required header fields detected empty still need routing
        for spec in c.template.prioritized():
            if spec.required == "always" and spec.id not in present_ids:
                inputs.append(FieldInput(spec, None, None, None, False, None, None, True))
        for sf in structure(c.template, inputs, code_sets, c.homologator, policy, rule_cfg):
            dec = sf.decision
            gate_reasons = [r for r in dec.reasons if r not in ("low_confidence", "no_threshold", "unreliable_confidence")]
            truth = gt_values.get((did, sf.inp.spec.id))
            records.append({
                "doc_id": did, "field_id": sf.inp.spec.id, "type": sf.inp.spec.type.value, "conf": sf.inp.confidence,
                "reliable": sf.inp.reliable, "route": dec.route.value, "reasons": list(dec.reasons), "gate_ok": not gate_reasons,
                "value": sf.value, "truth": truth, "correct": sf.value is not None and sf.value == truth,
            })
    return records


def calibration_summary(conf: np.ndarray, correct: np.ndarray, bins: int) -> dict[str, Any]:
    return {"ece": ece(conf, correct, bins), "reliability": reliability(conf, correct, bins), "n": int(conf.size),
            "accuracy": float(correct.mean()) if conf.size else float("nan"), "mean_conf": float(conf.mean()) if conf.size else float("nan")}
