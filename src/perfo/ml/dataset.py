"""Dataset by weak supervision (HU02): alignment with the transcribed history, literal labels, split and leakage check.

The web system stores normalized values (codes, 24 h times), not what was written. The literal training label of
each crop is derived from the record value: candidate renderings (formats, abbreviations, name forms) are generated
and the one closest to the baseline reading is chosen; crops with no close candidate are excluded with their cause.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from rapidfuzz.distance import Levenshtein

from perfo.domain.fields import FieldType
from perfo.domain.normalization import basic_normalize
from perfo.domain.states import DocumentState
from perfo.ml.metrics import literal


class LeakageError(Exception):
    pass


@dataclass
class LabeledCrop:
    doc_id: str
    crop_id: str
    sha256: str | None
    object_key: str
    field_id: str
    key: str
    field_type: str
    label_text: str
    label_value: str | None
    source: str  # weak | gt | correction | confirmed
    distance: float = 0.0
    operator: str | None = None
    split: str | None = None


@dataclass
class DatasetBuild:
    aligned_docs: list[str]
    excluded_docs: dict[str, str]  # doc_id -> cause
    items: list[LabeledCrop]
    excluded_crops: Counter
    doc_operator: dict[str, str]
    doc_key: dict[str, str]
    stats: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ history


def load_history(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Long-format export (registro_id, fecha, guardia, equipo, campo, valor) grouped by business key."""
    records: dict[str, dict[str, Any]] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rid = row["registro_id"]
            rec = records.setdefault(rid, {"registro_id": rid, "key": f"{row['fecha']}|{row['guardia']}|{row['equipo']}", "fields": {}})
            rec["fields"][row["campo"]] = row["valor"]
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records.values():
        by_key[rec["key"]].append(rec)
    return by_key


# ------------------------------------------------------------------ literal candidates


def _num_variants(value: str, key: str) -> list[str]:
    out = [value]
    try:
        v = float(value)
    except ValueError:
        return out
    if "." in value or key.endswith(("length_m", "deviation_m", "deviation_pct", "hm_start", "hm_end", "angle_executed")):
        for fmt in ("{:.1f}", "{:.2f}"):
            out.append(fmt.format(v))
    if v == int(v):
        out.append(str(int(v)))
    out += [o.replace(".", ",") for o in out if "." in o]
    if "angle" in key:
        out += [o + "°" for o in list(out)]
    return list(dict.fromkeys(out))


def _time_variants(value: str) -> list[str]:
    h, m = map(int, value.split(":"))
    h12 = h % 12 or 12
    suf = ["PM", " PM", " P.M.", "P"] if h >= 12 else ["AM", " AM", " A.M.", "A"]
    base = [f"{h:02d}:{m:02d}", f"{h}:{m:02d}", f"{h:02d}.{m:02d}", f"{h}.{m:02d}", f"{h12}:{m:02d}", f"{h12}.{m:02d}"]
    return list(dict.fromkeys(base + [f"{h12}:{m:02d}{s}" for s in suf] + [f"{h12}.{m:02d}{s}" for s in suf]))


def _date_variants(value: str) -> list[str]:
    d = date.fromisoformat(value)
    return list(dict.fromkeys([d.strftime("%d/%m/%Y"), d.strftime("%d/%m/%y"), f"{d.day}/{d.month}/{d.strftime('%y')}", d.strftime("%d-%m-%y"), f"{d.day}/{d.month}/{d.year}"]))


def _reverse_abbr(abbr: dict[str, str]) -> dict[str, list[str]]:
    rev: dict[str, list[str]] = defaultdict(list)
    for short, full in abbr.items():
        if full:
            rev[basic_normalize(full)].append(short)
    return rev


def _labor_variants(desc: str, abbr: dict[str, str]) -> list[str]:
    rev = _reverse_abbr(abbr)
    tokens = basic_normalize(desc).split()
    if not tokens:
        return []
    alts = [[t, *rev.get(t, [])] for t in tokens]
    out = []
    for combo in itertools.product(*alts):
        parts = list(combo)
        out.append(" ".join(parts))
        if len(parts) >= 2:
            out.append(parts[0] + "-" + " ".join(parts[1:]))
            out.append(parts[0] + ". " + " ".join(parts[1:]))
        if len(parts) >= 3 and parts[-2].isdigit():
            out.append(" ".join(parts[:-2]) + " " + parts[-2] + parts[-1])
            out.append(parts[0] + "-" + " ".join(parts[1:-2] + [parts[-2] + parts[-1]]))
    return list(dict.fromkeys(out))


def _equipment_variants(code: str, desc: str) -> list[str]:
    toks = [t for t in desc.replace("N°", "").split() if t]
    out = [code, code.replace("-", " ")]
    num = toks[-1] if toks and toks[-1].isdigit() else None
    for r in range(1, len(toks)):
        for sub in itertools.combinations(toks[:-1], r):
            if num:
                for n in {num, str(int(num))}:
                    for sep in (" ", "-", " N° "):
                        out.append(" ".join(sub) + sep + n)
    return list(dict.fromkeys(out))


def _person_variants(name: str) -> list[str]:
    if "," in name:
        surnames, given = [p.strip() for p in name.split(",", 1)]
    else:
        parts = name.split()
        given, surnames = parts[0], " ".join(parts[1:])
    s = surnames.split()
    g = given.split()[0]
    s1, s2 = s[0], (s[1] if len(s) > 1 else "")
    out = [f"{g[0]}. {s1}", f"{s1} {g[0]}.", f"{g} {s1}", f"{s1} {s2}", f"{g} {s1} {s2}", f"{s1} {s2} {g}", f"{s1} {g}", f"{g[0]} {s1}"]
    if s2:
        out.append(f"{g[0]}. {s1} {s2[0]}.")
    return list(dict.fromkeys(o.strip() for o in out))


class LiteralLabeler:
    def __init__(self, masters: Any, abbreviations: dict[str, dict[str, str]]):
        self.abbr = abbreviations
        self.items: dict[str, dict[str, Any]] = {}
        for t in ("labores", "equipos", "personal"):
            try:
                self.items[t] = {i.code: i for i in masters.get_master(t).items}
            except Exception:  # noqa: BLE001
                self.items[t] = {}
        self._cache: dict[tuple[str, str], list[str]] = {}

    def candidates(self, ftype: str, key: str, value: str, master_type: str | None) -> list[str]:
        ck = (key if ftype in ("numeric",) else ftype + (master_type or ""), value)
        if ck in self._cache:
            return self._cache[ck]
        if ftype == FieldType.NUMERIC.value:
            out = _num_variants(value, key)
        elif ftype == FieldType.TIME.value:
            out = _time_variants(value)
        elif ftype == FieldType.DATE.value:
            out = _date_variants(value)
        elif ftype == FieldType.CODE.value:
            out = {"D": ["D", "DIA"], "N": ["N", "NOCHE"]}.get(value, [value]) if key == "header.shift" else [value]
        elif ftype == FieldType.MASTER_REF.value and master_type:
            item = self.items.get(master_type, {}).get(value)
            if item is None:
                out = []
            elif master_type == "labores":
                out = _labor_variants(item.description, self.abbr.get("labores", {}))
            elif master_type == "equipos":
                out = _equipment_variants(item.code, item.description)
            else:
                out = _person_variants(item.description)
        else:
            out = [value]
        out = [literal(o) for o in out]
        self._cache[ck] = list(dict.fromkeys(out))
        return self._cache[ck]

    def label(self, ftype: str, key: str, value: str, master_type: str | None, reading: str | None) -> tuple[str | None, float]:
        cands = self.candidates(ftype, key, value, master_type)
        if not cands:
            return None, 1.0
        if len(cands) == 1 and not reading:
            return cands[0], 0.0
        r = literal(reading)
        best = min(cands, key=lambda c: (Levenshtein.normalized_distance(c, r), cands.index(c)))
        return best, float(Levenshtein.normalized_distance(best, r))


# ------------------------------------------------------------------ alignment


def build_weak_dataset(c: Any, history_csv: str | Path, baseline_version: str, max_distance: float, batch: str | None = "historical") -> DatasetBuild:
    history = load_history(history_csv)
    labeler = LiteralLabeler(c.masters, c.abbreviations)
    spec_of = {f.id: f for f in c.template.fields}
    docs = [d for d in c.repo.list_documents() if batch is None or d.metadata.get("batch") == batch]
    aligned, excluded, items = [], {}, []
    excluded_crops: Counter = Counter()
    doc_operator, doc_key = {}, {}
    for d in docs:
        if d.state == DocumentState.ERROR:
            excluded[d.id] = "imagen_no_corregible"
            continue
        if d.state == DocumentState.DUPLICADO_PENDIENTE:
            excluded[d.id] = "documento_duplicado"
            continue
        crops = c.repo.get_crops(d.id)
        if not crops:
            excluded[d.id] = "plantilla_no_alineada"
            continue
        if not d.business_key:
            excluded[d.id] = "sin_clave_de_negocio"
            continue
        recs = history.get(d.business_key, [])
        if not recs:
            excluded[d.id] = "sin_registro_en_historico"
            continue
        if len(recs) > 1:
            excluded[d.id] = "registro_no_unico"
            continue
        rec = recs[0]["fields"]
        aligned.append(d.id)
        doc_key[d.id] = d.business_key
        doc_operator[d.id] = rec.get("header.operator", "?")
        readings = {r.crop_id: r.text for r in c.repo.get_recognitions([cr.id for cr in crops], baseline_version)}
        for cr in crops:
            spec = spec_of[cr.field_id]
            value = rec.get(cr.field_id)
            if cr.is_empty:
                if value is not None:
                    excluded_crops["celda_vacia_con_valor"] += 1
                continue
            if cr.mark is not None:
                excluded_crops["marca_de_repeticion"] += 1
                continue
            if value is None:
                excluded_crops["sin_valor_en_registro"] += 1
                continue
            text, dist = labeler.label(spec.type.value, spec.key, value, spec.master_type, readings.get(cr.id))
            if text is None or dist > max_distance:
                excluded_crops["etiqueta_literal_no_determinable"] += 1
                continue
            items.append(LabeledCrop(d.id, cr.id, cr.sha256, cr.object_key, cr.field_id, spec.key, spec.type.value, text, value, "weak",
                                     round(dist, 4), doc_operator[d.id]))  # type: ignore[arg-type]
    return DatasetBuild(aligned, excluded, items, excluded_crops, doc_operator, doc_key)


# ------------------------------------------------------------------ split


def split_documents(
    docs: list[str], operator: dict[str, str], seed: int, ratios: tuple[float, float, float], unseen_operators: int,
    sprint1_sample: int, reference_subset: int, frozen_test: list[str] | None = None,
) -> dict[str, Any]:
    """Split by format, stratified by drilling operator. Validation halves: selection and calibration."""
    rng = random.Random(seed)
    docs = sorted(docs)
    frozen = set(frozen_test or [])
    pool = [d for d in docs if d not in frozen]
    by_op: dict[str, list[str]] = defaultdict(list)
    for d in pool:
        by_op[operator.get(d, "?")].append(d)

    # operators held out of training (test only); require >= 2 formats so a transcription error cannot pick a phantom operator
    eligible = sorted(o for o in by_op if len(by_op[o]) >= 2)
    unseen = sorted(rng.sample(eligible, k=min(unseen_operators, len(eligible)))) if unseen_operators else []
    test = list(frozen)
    for o in unseen:
        test += by_op.pop(o)

    remaining = [d for o in sorted(by_op) for d in by_op[o]]
    rng.shuffle(remaining)
    sprint1 = sorted(remaining[:sprint1_sample])
    s1 = set(sprint1)

    train, val = [], []
    carry_v = carry_t = 0.0
    _, r_val, r_test = ratios
    for o in sorted(by_op):
        group = [d for d in by_op[o] if d not in s1]
        rng.shuffle(group)
        n = len(group)
        carry_v += n * r_val
        carry_t += n * r_test
        k_v, k_t = int(carry_v), int(carry_t)
        carry_v -= k_v
        carry_t -= k_t
        k_v = min(k_v, n)
        k_t = min(k_t, n - k_v)
        val += group[:k_v]
        test += group[k_v : k_v + k_t]
        train += group[k_v + k_t :]

    val_sorted = sorted(val, key=lambda d: (operator.get(d, "?"), d))
    val_select = [d for i, d in enumerate(val_sorted) if i % 2 == 0]
    val_calib = [d for i, d in enumerate(val_sorted) if i % 2 == 1]
    test = sorted(set(test))
    ref_rng = random.Random(seed + 1)
    reference = sorted(ref_rng.sample(test, k=min(reference_subset, len(test))))
    return {
        "train": sorted(train), "val_select": sorted(val_select), "val_calib": sorted(val_calib), "test": test,
        "sprint1_sample": sprint1, "reference_subset": reference, "unseen_operators": unseen,
        "ratios": list(ratios), "seed": seed,
    }


def leakage_check(split: dict[str, Any], training_items: list[LabeledCrop], doc_sha: dict[str, str], doc_key: dict[str, str]) -> list[str]:
    """Fails when a test format (id, content hash or business key) reaches training, selection or calibration."""
    problems = []
    test = set(split["test"])
    used = set(split["train"]) | set(split["val_select"]) | set(split["val_calib"]) | {i.doc_id for i in training_items}
    for d in test & used:
        problems.append(f"documento de prueba en entrenamiento/calibración: {d[:8]}")
    test_sha = {doc_sha[d] for d in test if d in doc_sha}
    test_keys = {doc_key[d] for d in test if d in doc_key}
    for d in used - test:
        if doc_sha.get(d) in test_sha:
            problems.append(f"mismo contenido que un formato de prueba: {d[:8]}")
        if doc_key.get(d) in test_keys:
            problems.append(f"misma clave de negocio que un formato de prueba: {d[:8]}")
    return sorted(set(problems))


def snapshot_manifest(snapshot_id: str, kind: str, items: list[LabeledCrop], extra: dict[str, Any]) -> tuple[bytes, str]:
    payload = {"id": snapshot_id, "kind": kind, **extra, "items": [asdict(i) for i in items]}
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return data, hashlib.sha256(data).hexdigest()
