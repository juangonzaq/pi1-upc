"""PNG charts for the report: coverage–risk, reliability diagram and learning curve."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PALETTE = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2"]


def coverage_risk_plot(routing: dict[str, Any], risk_target: float, path: str | Path) -> str:
    fig, ax = plt.subplots(figsize=(7.5, 4.6), dpi=130)
    for i, (ftype, pts) in enumerate(sorted(routing.get("curve", {}).items())):
        pts = sorted(pts, key=lambda p: p["coverage"])
        ax.plot([p["coverage"] for p in pts], [p["risk"] for p in pts], color=PALETTE[i % len(PALETTE)], lw=1.6, label=ftype)
        bt = routing.get("by_type", {}).get(ftype, {})
        if bt.get("threshold") is not None:
            ax.scatter([bt["coverage"]], [bt["risk"]], color=PALETTE[i % len(PALETTE)], s=36, zorder=5, edgecolor="white")
    ax.axhline(risk_target, color="#6b7280", ls="--", lw=1, label=f"riesgo objetivo {risk_target:.0%}")
    ax.set_xlabel("Cobertura (fracción aceptada automáticamente)")
    ax.set_ylabel("Riesgo (error residual en aceptados)")
    ax.set_title("Curva cobertura–riesgo por tipo de campo (conjunto de prueba)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, max(0.05, min(0.6, max((p["risk"] for pts in routing.get("curve", {}).values() for p in pts), default=0.05) * 1.1)))
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def reliability_plot(routing: dict[str, Any], path: str | Path) -> str:
    fig, ax = plt.subplots(figsize=(5.2, 5.0), dpi=130)
    ax.plot([0, 1], [0, 1], color="#9ca3af", ls="--", lw=1, label="calibración perfecta")
    for key, label, color in (("reliability_uncalibrated", f"sin calibrar (ECE {routing.get('ece_uncalibrated', float('nan')):.3f})", "#d97706"),
                              ("reliability", f"calibrado (ECE {routing.get('ece', float('nan')):.3f})", "#2563eb")):
        bins = [b for b in routing.get(key, []) if b["n"]]
        ax.plot([b["conf"] for b in bins], [b["acc"] for b in bins], marker="o", ms=4, color=color, lw=1.5, label=label)
    ax.set_xlabel("Confianza media del bin")
    ax.set_ylabel("Exactitud observada")
    ax.set_title("Diagrama de fiabilidad (15 bins, prueba)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def learning_curve_plot(points: list[dict[str, Any]], path: str | Path) -> str:
    """points: [{label, examples, cer, coverage?}] in chronological order."""
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=130)
    xs = [p["examples"] for p in points]
    ax.plot(xs, [p["cer"] for p in points], marker="o", color="#2563eb", lw=1.8, label="CER (prueba congelada)")
    for p in points:
        ax.annotate(p["label"], (p["examples"], p["cer"]), textcoords="offset points", xytext=(4, 6), fontsize=8)
    if any(p.get("coverage") is not None for p in points):
        ax2 = ax.twinx()
        cp = [p for p in points if p.get("coverage") is not None]
        ax2.plot([p["examples"] for p in cp], [p["coverage"] for p in cp], marker="s", color="#16a34a", lw=1.2, ls="--", label="cobertura automática")
        ax2.set_ylabel("Cobertura automática")
        ax2.set_ylim(0, 1)
        ax2.legend(fontsize=8, loc="upper center")
    ax.set_xlabel("Ejemplos etiquetados acumulados")
    ax.set_ylabel("CER")
    ax.set_title("Curva de aprendizaje")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return str(path)
