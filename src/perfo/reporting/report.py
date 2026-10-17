"""E2E report (Markdown + JSON + PNG) with IE1–IE5 against their targets and probable causes when a target is missed."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from perfo.domain.routing import describe
from perfo.reporting.plots import coverage_risk_plot, learning_curve_plot, reliability_plot

SYNTH_NOTE = "Resultados sobre datos sintéticos: validan el flujo, no los indicadores del proyecto"


def _n(x: Any) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def pct(x: Any, d: int = 1) -> str:
    return "—" if _n(x) else f"{x:.{d}f} %"


def num(x: Any, d: int = 4) -> str:
    return "—" if _n(x) else f"{x:.{d}f}"


def ok(flag: bool | None) -> str:
    return "—" if flag is None else ("✅ cumple" if flag else "❌ no cumple")


def _ie4_cause(routing: dict[str, Any], cal: dict[str, Any]) -> str:
    missing = [t for t, v in (cal.get("thresholds") or {}).items() if v is None]
    parts = []
    risky = {t: v["risk"] for t, v in (routing.get("by_type") or {}).items() if v.get("threshold") is not None and v.get("risk", 0) > 0.01}
    if risky:
        parts.append("los umbrales elegidos en la mitad de calibración no generalizan: riesgo residual en prueba "
                     + ", ".join(f"{t} {r * 100:.1f} %" for t, r in risky.items())
                     + " (> 1 %; pocas muestras de calibración y etiquetas débiles con errores del histórico)")
    if missing:
        reasons = {t: (cal.get("threshold_details", {}).get(t, {}) or {}).get("reason") for t in missing}
        ns = {t: (cal.get("threshold_details", {}).get(t, {}) or {}).get("n") for t in missing}
        parts.append("sin umbral calibrado para " + ", ".join(f"{t} ({describe_reason(reasons[t])}, n={ns[t]})" for t in missing))
    top = sorted((routing.get("review_reasons") or {}).items(), key=lambda kv: -kv[1])[:3]
    if top:
        parts.append("motivos de revisión más frecuentes: " + ", ".join(f"{describe(k)} ({v})" for k, v in top))
    if (cal.get("calibration_formats") or 0) < 20:
        parts.append(f"mitad de calibración pequeña ({cal.get('calibration_formats')} formatos)")
    return "; ".join(parts) or "—"


def describe_reason(r: str | None) -> str:
    return {"soporte_insuficiente": "soporte insuficiente", "riesgo_no_alcanzable": "ningún umbral con riesgo ≤ 1 %",
            "sin_muestras": "sin muestras", "muestras_insuficientes": "muestras insuficientes",
            "sin_temperatura": "sin temperatura: muestras de calibración insuficientes", "ok": "ok"}.get(r or "", r or "—")


def write_report(c: Any, R: dict[str, Any], run_id: str) -> dict[str, str]:
    out_dir = c.cfg.resolve("reports")
    out_dir.mkdir(exist_ok=True)
    stem = f"e2e_{run_id}"
    th = c.cfg["thresholds"]
    tg = th["targets"]

    ev = R.get("evaluation_initial", {})
    models = list(ev.get("models", {}))
    base_v, ft_v = (models + [None, None])[:2]
    mb = ev.get("models", {}).get(base_v, {})
    mf = ev.get("models", {}).get(ft_v, {})
    cyc = R.get("cycle", {}) or {}
    cyc_ev = R.get("cycle_evaluation") or {}
    cand = cyc.get("candidate")
    mc = (cyc_ev.get("models") or {}).get(cand, {}) if cand else {}
    routing_final = cyc.get("routing") if (cyc.get("promotion") or {}).get("promoted") else R.get("routing_initial", {})
    routing_final = routing_final or R.get("routing_initial", {})

    # ------------------------------------------------------------ indicators
    ie1 = R["template"]["prioritized_fields"]
    ds = R.get("dataset", {})
    ie2_raw = ds.get("IE2_pct")
    ie3 = ev.get("relative_cer_reduction_pct")
    rt = R.get("routing_initial", {})
    ie4, risk4 = rt.get("IE4_pct"), rt.get("residual_risk")
    ie5 = cyc.get("ie5_pct")
    ie_rows = [
        ("IE1", "Campos priorizados (matriz)", f"≥ {tg['IE1_fields']}", f"{ie1} de {R['template']['candidate_fields']} candidatos", ie1 >= tg["IE1_fields"],
         "Matriz propuesta con la fórmula 0,5R+0,3F+0,2L ≥ 3,5; pendiente de validación del PO en acta (HU01)."),
        ("IE2", "Cumplimiento del dataset requerido", "100 %", f"{min(100.0, ie2_raw or 0):.0f} % ({ds.get('aligned_formats')} de {ds.get('required_formats')} requeridos)",
         (ie2_raw or 0) >= tg["IE2_pct"],
         f"Requeridos del perfil (supuesto; el TB1 fija 2 000 para datos reales). Excluidos: {ds.get('excluded_by_cause')}."),
        ("IE3", "Reducción de CER por ajuste fino", f"≥ {tg['IE3_pct']} %", f"{pct(ie3)} (CER {num(mb.get('cer'))} → {num(mf.get('cer'))})",
         None if _n(ie3) else ie3 >= tg["IE3_pct"],
         "—" if not _n(ie3) and ie3 >= tg["IE3_pct"] else "Pocos pasos de entrenamiento o dataset pequeño en este perfil; ver curva de selección."),
        ("IE4", "Reducción de la intervención manual (cobertura automática con error ≤ 1 %)", f"≥ {tg['IE4_pct']} % con error ≤ {tg['IE4_residual_error'] * 100:.0f} %",
         f"{pct(ie4)} con error residual {pct(None if _n(risk4) else risk4 * 100, 2)}",
         None if _n(ie4) else (ie4 >= tg["IE4_pct"] and (risk4 or 0) <= tg["IE4_residual_error"]),
         _ie4_cause(rt, R.get("calibration_initial", {})) if _n(ie4) or ie4 < tg["IE4_pct"] or (risk4 or 0) > tg["IE4_residual_error"] else "—"),
        ("IE5", "Reducción de CER por aprendizaje continuo", f"≥ {tg['IE5_pct']} %",
         f"{pct(ie5)} (CER {num(mf.get('cer') if not cyc_ev else (cyc_ev.get('models') or {}).get(cyc.get('active_before'), {}).get('cer'))} → {num(mc.get('cer'))})" if cand else "— (sin candidato)",
         None if _n(ie5) else ie5 >= tg["IE5_pct"],
         "—" if not _n(ie5) and ie5 >= tg["IE5_pct"] else f"Correcciones disponibles: {(cyc.get('stats') or {}).get('corrections')}; estado del ciclo: {cyc.get('status')}."),
    ]

    # ------------------------------------------------------------ plots
    pngs = {}
    risk_t = float(th["risk_target"])
    pngs["cobertura_riesgo"] = coverage_risk_plot(routing_final, risk_t, out_dir / f"{stem}_cobertura_riesgo.png")
    pngs["fiabilidad"] = reliability_plot(routing_final, out_dir / f"{stem}_fiabilidad.png")
    lc = [{"label": "base", "examples": 0, "cer": mb.get("cer"), "coverage": 0.0}]
    n_train = (ds.get("by_split_crops") or {}).get("train", 0)
    if mf:
        lc.append({"label": "ajustado", "examples": n_train, "cer": mf.get("cer"), "coverage": rt.get("coverage")})
    if mc:
        st = cyc.get("stats") or {}
        lc.append({"label": "ciclo 1", "examples": n_train + st.get("corrections", 0) + st.get("confirmed_positives_used", 0),
                   "cer": mc.get("cer"), "coverage": (cyc.get("routing") or {}).get("coverage")})
    lc = [p for p in lc if not _n(p["cer"])]
    pngs["curva_aprendizaje"] = learning_curve_plot(lc, out_dir / f"{stem}_curva_aprendizaje.png")
    R["learning_curve"] = lc

    # ------------------------------------------------------------ markdown
    L: list[str] = []
    L.append("# Reporte E2E · Perforación IA")
    L.append("")
    L.append(f"> ⚠️ **{SYNTH_NOTE}.**")
    L.append("")
    L.append(f"- Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')} · perfil `{R['profile']}` · dispositivo `{R['device']}` · semilla {R['config']['seed']}")
    L.append(f"- Modelo base: `{R['config']['base_checkpoint']}` · agregación de confianza: `{R['config']['aggregation']}`")
    L.append(f"- Duración total: {R['total_seconds'] / 60:.1f} min · modelo activo al cierre: `{R.get('active_model')}`")
    L.append(f"- Espacio de trabajo: `{R['workspace']}`")
    L.append("")
    L.append("## Indicadores del proyecto (IE1–IE5)")
    L.append("")
    L.append("| Código | Indicador | Meta | Resultado | Estado | Causa probable / nota |")
    L.append("|---|---|---|---|---|---|")
    for code, name, meta, res, flag, note in ie_rows:
        L.append(f"| {code} | {name} | {meta} | {res} | {ok(flag)} | {note} |")
    L.append("")
    L.append("## Métricas de calidad")
    L.append("")
    hb = R.get("homologation_baseline", {})
    hl = hb.get("literal", {})
    L.append("| Métrica | Meta | Resultado | Estado |")
    L.append("|---|---|---|---|")
    L.append(f"| ECE en prueba, modelo ajustado (15 bins, correctitud del valor) | ≤ {th['ece_target']} | {num(rt.get('ece'))} (sin calibrar {num(rt.get('ece_uncalibrated'))}; sobre texto literal {num(rt.get('ece_text'))}) | {ok(None if _n(rt.get('ece')) else rt['ece'] <= th['ece_target'])} |")
    if cyc.get("routing"):
        ce = cyc["routing"].get("ece")
        L.append(f"| ECE en prueba, candidato del ciclo | ≤ {th['ece_target']} | {num(ce)} | {ok(None if _n(ce) else ce <= th['ece_target'])} |")
    L.append(f"| Homologación Exactitud@1 (texto literal, HU08) | ≥ {tg['acc_at_1'] * 100:.0f} % | {pct(None if _n(hl.get('acc_at_1')) else hl['acc_at_1'] * 100)} (n={hl.get('n')}) | {ok(None if _n(hl.get('acc_at_1')) else hl['acc_at_1'] >= tg['acc_at_1'])} |")
    L.append(f"| Homologación Recall@3 (texto literal, HU08) | ≥ {tg['recall_at_3'] * 100:.0f} % | {pct(None if _n(hl.get('recall_at_3')) else hl['recall_at_3'] * 100)} | {ok(None if _n(hl.get('recall_at_3')) else hl['recall_at_3'] >= tg['recall_at_3'])} |")
    for label, m in (("modelo base", mb), ("ajustado", mf), ("ciclo", mc)):
        h = (m or {}).get("homologation") or {}
        if h:
            L.append(f"| Homologación sobre texto reconocido ({label}) | informativo | Exactitud@1 {pct(h['acc_at_1'] * 100)} · Recall@3 {pct(h['recall_at_3'] * 100)} | — |")
    tb = (R.get("baseline_preliminary") or {}).get("times", {})
    to = (R.get("operational") or {}).get("times", {})
    L.append(f"| Tiempo por formato, modelo base (p50 / p95) | p95 ≤ {th['latency_p95_budget_s']} s | {num(tb.get('p50_s'), 2)} s / {num(tb.get('p95_s'), 2)} s | {ok(None if _n(tb.get('p95_s')) else tb['p95_s'] <= th['latency_p95_budget_s'])} |")
    L.append(f"| Tiempo por formato, modelo activo en el lote operativo (p50 / p95) | p95 ≤ {th['latency_p95_budget_s']} s | {num(to.get('p50_s'), 2)} s / {num(to.get('p95_s'), 2)} s | {ok(None if _n(to.get('p95_s')) else to['p95_s'] <= th['latency_p95_budget_s'])} |")
    L.append("")
    L.append("### CER por tipo de campo (conjunto de prueba congelado)")
    L.append("")
    L.append("| Tipo | Modelo base | Ajustado | Ciclo |")
    L.append("|---|---|---|---|")
    for t in sorted(set((mb.get("by_type") or {}) | (mf.get("by_type") or {}))):
        L.append(f"| {t} | {num((mb.get('by_type') or {}).get(t, {}).get('cer'))} | {num((mf.get('by_type') or {}).get(t, {}).get('cer'))} | {num((mc.get('by_type') or {}).get(t, {}).get('cer')) if mc else '—'} |")
    L.append(f"| **Global** | {num(mb.get('cer'))} | {num(mf.get('cer'))} | {num(mc.get('cer')) if mc else '—'} |")
    L.append(f"| WER | {num(mb.get('wer'))} | {num(mf.get('wer'))} | {num(mc.get('wer')) if mc else '—'} |")
    L.append(f"| Exactitud por campo (valor) | {num(mb.get('field_accuracy'), 3)} | {num(mf.get('field_accuracy'), 3)} | {num(mc.get('field_accuracy'), 3) if mc else '—'} |")
    L.append(f"| CER operadores no vistos | {num(mb.get('cer_unseen_operators'))} | {num(mf.get('cer_unseen_operators'))} | {num(mc.get('cer_unseen_operators')) if mc else '—'} |")
    L.append(f"| CER subconjunto de referencia (doble digitación) | {num(mb.get('cer_reference_subset'))} | {num(mf.get('cer_reference_subset'))} | {num(mc.get('cer_reference_subset')) if mc else '—'} |")
    L.append("")
    L.append("### Enrutamiento por tipo de campo (prueba, modelo ajustado)")
    L.append("")
    cal = R.get("calibration_initial", {})
    L.append("| Tipo | T | Umbral | Campos | Aceptados | Cobertura | Riesgo residual | Motivo si no hay umbral |")
    L.append("|---|---|---|---|---|---|---|---|")
    for t, v in (rt.get("by_type") or {}).items():
        reason = describe_reason(((cal.get("threshold_details") or {}).get(t) or {}).get("reason")) if v.get("threshold") is None else ""
        thr = "—" if v.get("threshold") is None else f"{v['threshold']:.3f}"
        L.append(f"| {t} | {(cal.get('temperatures') or {}).get(t, '—')} | {thr} | {v['n']} | {v['auto']} | {pct(v['coverage'] * 100)} | {pct(v['risk'] * 100, 2)} | {reason} |")
    L.append("")
    L.append("Motivos de revisión: " + ", ".join(f"{describe(k)} ({v})" for k, v in sorted((rt.get("review_reasons") or {}).items(), key=lambda kv: -kv[1])))
    L.append("")
    op = R.get("operational") or {}
    if op:
        L.append(f"**Lote operativo** (modelo `{op.get('model')}`): cobertura automática {pct(op.get('coverage', 0) * 100)} con error residual "
                 f"{pct(op.get('residual_risk', 0) * 100, 2)}; el validador tocó {op.get('touched')} de {op.get('fields')} campos y corrigió {op.get('corrected')}; "
                 f"alias aprendidos: {op.get('aliases')}.")
        L.append("")
    L.append("## Gráficos")
    L.append("")
    for k, p in pngs.items():
        L.append(f"![{k}]({Path(p).name})")
        L.append("")
    L.append("## Flujo ejecutado")
    L.append("")
    L.append("| Paso | Etapa | Estado | Duración | Detalle |")
    L.append("|---|---|---|---|---|")
    for s in R["steps"]:
        det = json.dumps({k: v for k, v in s["detail"].items() if k not in ("markdown", "json")}, ensure_ascii=False, default=str)
        det = det if len(det) < 380 else det[:377] + "…"
        L.append(f"| {s['n']} | {s['name']} | {s['status']} | {s['seconds']:.1f} s | `{det}` |")
    L.append("")
    L.append("## Dataset por supervisión débil")
    L.append("")
    L.append(f"- Formatos alineados: {ds.get('aligned_formats')} · excluidos: {ds.get('excluded_by_cause')}")
    L.append(f"- Recortes etiquetados: {ds.get('labeled_crops')} · excluidos: {ds.get('excluded_crops')}")
    L.append(f"- Partición (formatos): {ds.get('by_split_formats')} · operadores no vistos: {ds.get('unseen_operators')}")
    L.append(f"- Prueba de fuga: {'sin fugas' if not ds.get('leakage') else ds.get('leakage')}")
    L.append("")
    L.append("## Compuertas de promoción")
    L.append("")
    for title, block in (("Activación del modelo ajustado (obligatorias G1, G2, G6)", R.get("gates_initial", {})),
                         ("Ciclo de aprendizaje (obligatorias G1–G6)", {"gates": cyc.get("gates"), "promotion": cyc.get("promotion")})):
        L.append(f"### {title}")
        L.append("")
        if not block.get("gates"):
            L.append(f"Sin evaluación ({cyc.get('status')}).")
            L.append("")
            continue
        L.append("| Compuerta | Estado | Detalle |")
        L.append("|---|---|---|")
        for g in block["gates"]:
            L.append(f"| {g['gate']} {g['name']} | {'✅' if g['passed'] else '❌'} | {g['detail']} |")
        prom = block.get("promotion") or {}
        L.append("")
        L.append(f"Resultado: {'**promovido**' if prom.get('promoted') else '**no promovido**; el modelo activo no cambia'}"
                 + (f" (fallaron {prom.get('failed')})" if prom.get("failed") else ""))
        L.append("")
    if cyc.get("stats"):
        st = cyc["stats"]
        L.append(f"Snapshot del ciclo `{cyc.get('snapshot')}`: {st.get('corrections')} correcciones (×{st.get('correction_weight')}), "
                 f"{st.get('confirmed_positives_used')} confirmados, {st.get('replay')} de replay; excluidos {st.get('excluded')}.")
        L.append("")
    L.append("## Registro de modelos")
    L.append("")
    for m in R.get("registry", []):
        L.append(f"- `{m['version']}` · {m['status']} · padre `{m['parent'] or '-'}`")
    L.append("")
    L.append("## Limitaciones de esta corrida")
    L.append("")
    L.append("- La escritura es simulada con fuentes manuscritas: el ajuste fino aprende esas fuentes y la mejora no se trasladará tal cual a la escritura real.")
    L.append("- Las variantes de labores se generan con el mismo diccionario de abreviaturas que usa la homologación; por eso la Exactitud@1 sobre texto literal es casi perfecta.")
    L.append("- Las etiquetas de entrenamiento y calibración provienen del histórico (supervisión débil, con errores de transcripción inyectados); la prueba usa el ground truth (equivale a la doble digitación).")
    L.append("- En el perfil smoke la mitad de calibración es pequeña: varios tipos de campo no alcanzan el soporte mínimo y se envían a revisión (nunca se aceptan sin evidencia).")
    L.append("- La plantilla v1 describe la geometría sintética; debe recalibrarse sobre el formato físico real.")
    L.append("")
    md = out_dir / f"{stem}.md"
    md.write_text("\n".join(L), encoding="utf-8")
    js = out_dir / f"{stem}.json"
    js.write_text(json.dumps(R, indent=1, ensure_ascii=False, default=str))
    return {"markdown": str(md), "json": str(js), **{f"png_{k}": v for k, v in pngs.items()}}
