"""Evaluation metrics: CER/WER, field accuracy, ECE, reliability, coverage–risk and threshold selection."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import jiwer
import numpy as np


def literal(text: str | None) -> str:
    """Literal text normalization used for training and CER: uppercase, single spaces.

    Case is not evaluated because typing and homologation are case-insensitive and handwritten case is ambiguous.
    """
    return " ".join((text or "").upper().split())


def cer(refs: list[str], hyps: list[str]) -> float:
    pairs = [(r, h) for r, h in zip(refs, hyps, strict=True) if r]
    if not pairs:
        return float("nan")
    out = jiwer.process_characters([p[0] for p in pairs], [p[1] if p[1] else "" for p in pairs])
    n = sum(len(p[0]) for p in pairs)
    return (out.substitutions + out.deletions + out.insertions) / n


def wer(refs: list[str], hyps: list[str]) -> float:
    pairs = [(r, h) for r, h in zip(refs, hyps, strict=True) if r]
    if not pairs:
        return float("nan")
    out = jiwer.process_words([p[0] for p in pairs], [p[1] for p in pairs])
    n = sum(len(p[0].split()) for p in pairs)
    return (out.substitutions + out.deletions + out.insertions) / n


def grouped_cer(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    out = {}
    for g, rs in sorted(groups.items()):
        refs = [r["ref"] for r in rs]
        hyps = [r["hyp"] for r in rs]
        out[g] = {"cer": cer(refs, hyps), "n": len(rs), "exact": float(np.mean([r["ref"] == r["hyp"] for r in rs]))}
    return out


def ece(conf: np.ndarray, correct: np.ndarray, bins: int = 15) -> float:
    conf, correct = np.asarray(conf, float), np.asarray(correct, float)
    if conf.size == 0:
        return float("nan")
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1], right=True), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.sum() / conf.size * abs(correct[m].mean() - conf[m].mean())
    return float(total)


def reliability(conf: np.ndarray, correct: np.ndarray, bins: int = 15) -> list[dict[str, float]]:
    conf, correct = np.asarray(conf, float), np.asarray(correct, float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1], right=True), 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        out.append({"lo": float(edges[b]), "hi": float(edges[b + 1]), "n": int(m.sum()),
                    "conf": float(conf[m].mean()) if m.any() else None, "acc": float(correct[m].mean()) if m.any() else None})
    return out


def nll_binary(conf: np.ndarray, correct: np.ndarray) -> float:
    p = np.clip(np.asarray(conf, float), 1e-6, 1 - 1e-6)
    y = np.asarray(correct, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def coverage_risk(conf: np.ndarray, correct: np.ndarray, gate_ok: np.ndarray | None = None) -> list[dict[str, float]]:
    """Coverage and risk for every candidate threshold (accepted = conf >= thr and gates pass)."""
    conf, correct = np.asarray(conf, float), np.asarray(correct, bool)
    gate = np.ones_like(correct) if gate_ok is None else np.asarray(gate_ok, bool)
    n = conf.size
    out = []
    for thr in sorted(set(np.round(conf, 6).tolist()) | {1.000001}):
        acc = (conf >= thr) & gate
        k = int(acc.sum())
        out.append({"threshold": float(thr), "coverage": k / n if n else 0.0, "risk": float((~correct[acc]).mean()) if k else 0.0, "accepted": k})
    return out


def choose_threshold(conf: np.ndarray, correct: np.ndarray, gate_ok: np.ndarray | None, target_risk: float, min_support: int) -> dict[str, Any]:
    """Highest-coverage point whose residual error among accepted fields is <= target_risk."""
    curve = coverage_risk(conf, correct, gate_ok)
    feasible = [p for p in curve if p["accepted"] >= min_support and p["risk"] <= target_risk]
    if not feasible:
        reason = "sin_muestras" if np.asarray(conf).size == 0 else (
            "soporte_insuficiente" if max((p["accepted"] for p in curve), default=0) < min_support else "riesgo_no_alcanzable"
        )
        return {"threshold": None, "coverage": 0.0, "risk": None, "accepted": 0, "reason": reason, "n": int(np.asarray(conf).size)}
    best = max(feasible, key=lambda p: (p["coverage"], -p["threshold"]))
    return {**best, "reason": "ok", "n": int(np.asarray(conf).size)}


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else float("nan")
