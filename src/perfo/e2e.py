"""End-to-end test flow (`perfo e2e --profile smoke|full`): from synthetic images to the learning cycle and the report."""

from __future__ import annotations

import json
import random
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from perfo.bootstrap import Container
from perfo.config import load_config


class StepLog:
    def __init__(self, log: Any):
        self.steps: list[dict[str, Any]] = []
        self.log = log
        self._t0 = 0.0
        self._cur: dict[str, Any] | None = None

    def start(self, n: int, name: str) -> None:
        self._t0 = time.perf_counter()
        self._cur = {"n": n, "name": name, "status": "ok", "detail": {}}
        self.log(f"\n[{n:>2}/16] {name}")

    def done(self, status: str = "ok", seconds: float | None = None, **detail: Any) -> None:
        assert self._cur is not None
        elapsed = time.perf_counter() - self._t0 if seconds is None else seconds
        self._cur.update(status=status, seconds=round(elapsed, 2), detail=detail)
        self.steps.append(self._cur)
        self.log(f"        → {status} ({self._cur['seconds']:.1f} s)")


def _f(x: Any) -> Any:
    if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
        return None
    return x


def run_e2e(profile: str, device: str | None = None, workspace: str | None = None, log: Any = print) -> dict[str, Any]:
    from perfo.application.validation import ValidationBlocked
    from perfo.ml.evaluate import crop_items, field_accuracy, form_times, homologation_cases, homologation_eval, summarize_text, text_rows
    from perfo.ml.groundtruth import GroundTruth
    from perfo.ml.learning_cycle import evaluate_gates, promote, run_cycle
    from perfo.ml.pipeline import build_dataset, calibrate, evaluate_models, evaluate_routing, finetune
    from perfo.reporting.report import write_report
    from perfo.synth.generator import generate_corpus
    from perfo.synth.oracle import oracle_decisions, shift_boss_decision

    t_all = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_cfg = load_config(profile)
    ws = Path(workspace) if workspace else base_cfg.resolve(base_cfg["workspace"]) / run_id
    overrides: dict[str, Any] = {"workspace": str(ws)}
    if device:
        overrides["device"] = device
    c = Container(load_config(profile, overrides))
    S = StepLog(log)
    R: dict[str, Any] = {"run_id": run_id, "profile": profile, "workspace": str(c.workspace), "device": c.device(), "config": {
        "base_checkpoint": c.cfg["model"]["base_checkpoint"], "aggregation": c.cfg["model"]["aggregation"], "seed": c.cfg["seed"],
        "synth": c.cfg["synth"], "training": c.cfg["training"], "cycle": c.cfg["cycle"], "dataset": c.cfg["dataset"]}}
    log(f"Perforación IA · e2e perfil '{profile}' · dispositivo {c.device()} · espacio {c.workspace}")

    # 1 ---------------------------------------------------------------- synthetic corpus
    S.start(1, "Generar corpus sintético")
    man = generate_corpus(dict(c.cfg["synth"]), c.template_definition, c.workspace / "synth", c.cfg.resolve("data/fonts"), int(c.cfg["seed"]))
    gt = GroundTruth(c.workspace / "synth")
    n_hist = sum(1 for f in man["forms"] if f["batch"] == "historical")
    S.done(historical=n_hist, operational=len(man["forms"]) - n_hist, fonts=man["fonts_source"], n_fonts=len(man["fonts"]),
           history_records=man["history_stats"]["records"], history_errors=man["history_stats"]["field_errors"],
           history_missing=len(man["history_stats"]["missing"]), history_duplicated=len(man["history_stats"]["duplicated"]))

    # 2 ---------------------------------------------------------------- masters
    S.start(2, "Importar maestros (validación y versionado)")
    mdir = c.workspace / "synth" / "masters"
    versions = {}
    for mtype in ("labores", "equipos", "personal", "codigos"):
        v, errs = c.masters.import_master(mtype, str(mdir / f"{mtype}.csv"))
        versions[mtype] = v
    bad_v, bad_errs = c.masters.import_master("labores", str(mdir / "labores_invalido.csv"))
    kept = c.masters.get_master("labores").version
    S.done(versions=versions, invalid_rejected=bad_v is None, invalid_errors=bad_errs, active_after_invalid=kept)

    # 3 ---------------------------------------------------------------- ingestion
    S.start(3, "Ingestar formatos (SHA-256, duplicados, idempotencia)")
    outcomes: Counter = Counter()
    first_hist = None
    for f in man["forms"]:
        p = c.workspace / "synth" / f["image"]
        meta = {"source_name": p.name, "uploaded_by": "secretaria.sim", "business_key": f["metadata_key"], "batch": f["batch"], "form_id": f["form_id"]}
        did = c.ingestion.submit(p.read_bytes(), meta)
        outcomes[c.ingestion.last_outcome] += 1
        if f["batch"] == "historical":
            first_hist = first_hist or p
            c.ingestion.enqueue(did, "secretaria.sim")
    if man.get("rescan"):
        src = next(f for f in man["forms"] if f["form_id"] == man["rescan"]["form_id"])
        p = c.workspace / "synth" / man["rescan"]["image"]
        c.ingestion.submit(p.read_bytes(), {"source_name": p.name, "uploaded_by": "secretaria.sim", "business_key": src["metadata_key"],
                                            "batch": "operational", "form_id": src["form_id"], "rescan": True})
        outcomes["reescaneo_" + c.ingestion.last_outcome] += 1
    n_docs = len(c.repo.list_documents())
    if first_hist:
        c.ingestion.submit(first_hist.read_bytes(), {"source_name": first_hist.name, "uploaded_by": "secretaria.sim"})
        outcomes["recarga_" + c.ingestion.last_outcome] += 1
    idempotent = len(c.repo.list_documents()) == n_docs
    S.done(outcomes=dict(outcomes), documents=len(c.repo.list_documents()), idempotent_reupload=idempotent)

    # 4-8 -------------------------------------------------------------- processing with the baseline
    base = c.ensure_baseline()
    proc = c.processor()
    hist_docs = c.repo.list_documents(states=["EN_COLA"])
    outs: list[Any] = []
    # the queue port drains the jobs enqueued at intake (in-process adapter; Celery + Redis in production)
    c.queue.register("process_document", lambda payload: outs.append(proc.process(payload["document_id"])))
    t_proc = time.perf_counter()
    c.queue.run_pending()
    t_proc = time.perf_counter() - t_proc
    states = Counter(o.state for o in outs)
    stage = {k: [o.stages.get(k, 0.0) for o in outs if k in o.stages] for k in ("preprocess", "segment", "recognize", "structure")}

    S.start(4, "Control de calidad y preprocesamiento")
    blocked = [o.cause for o in outs if o.state == "ERROR"]
    S.done(seconds=sum(stage["preprocess"]), processed=len(outs), blocked=len(blocked), causes=blocked, p50_s=_f(float(np.median(stage["preprocess"])) if stage["preprocess"] else None))
    S.start(5, "Segmentar con la plantilla v1 (alineación con tolerancia)")
    misaligned = [o.cause for o in outs if o.state == "EN_REVISION" and not o.run_id]
    n_crops = sum(len(c.repo.get_crops(d.id)) for d in hist_docs)
    S.done(seconds=sum(stage["segment"]), aligned=len(outs) - len(blocked) - len(misaligned), out_of_tolerance=len(misaligned), causes=misaligned, crops=n_crops,
           tolerance_px=c.template.alignment.tolerance_px)
    S.start(6, "Reconocer con el modelo base (línea base)")
    processed_ids = [o.document_id for o in outs if o.run_id]
    items_all = crop_items(c, processed_ids, gt)
    recs = {r.crop_id: r for r in c.repo.get_recognitions([i.crop_id for i in items_all], base.version)}
    texts_all = [recs[i.crop_id].text if i.crop_id in recs else None for i in items_all]
    prelim = summarize_text(text_rows(items_all, texts_all))
    times_base = form_times(str(c.workspace / "telemetry" / "events.jsonl"), base.version)
    R["baseline_preliminary"] = {"cer": prelim["cer"], "wer": prelim["wer"], "by_type": prelim["by_type"], "n": prelim["n"], "times": times_base}
    S.done(seconds=sum(stage["recognize"]), model=base.version, cer=_f(prelim["cer"]), by_type={k: _f(v["cer"]) for k, v in prelim["by_type"].items()},
           p50_s=_f(times_base["p50_s"]), p95_s=_f(times_base["p95_s"]), seconds_total=round(t_proc, 1))
    S.start(7, "Estructurar y aplicar reglas")
    viol: Counter = Counter()
    routes: Counter = Counter()
    for did in processed_ids:
        for v in c.repo.get_field_values(did):
            routes[v.route] += 1
            for x in v.violations:
                viol[x["rule"]] += 1
    S.done(seconds=sum(stage["structure"]), fields=sum(routes.values()), routes=dict(routes), violations=dict(viol.most_common()))
    S.start(8, "Homologar con los maestros")
    hom_lit = homologation_eval(c.homologator, homologation_cases(c, items_all))
    hom_rec = homologation_eval(c.homologator, homologation_cases(c, items_all, texts_all))
    R["homologation_baseline"] = {"literal": hom_lit, "recognized": hom_rec}
    S.done(acc_at_1_literal=hom_lit["acc_at_1"], recall_at_3_literal=hom_lit["recall_at_3"], auto_resolved_literal=hom_lit["auto_resolved"],
           acc_at_1_recognized=hom_rec["acc_at_1"], recall_at_3_recognized=hom_rec["recall_at_3"], n=hom_lit["n"])

    # 9 ---------------------------------------------------------------- dataset
    S.start(9, "Construir el dataset por alineación con el histórico")
    ds = build_dataset(c, c.workspace / "synth" / man["history_csv"], log=log)
    split = c.repo.get_split("split-v1")
    R["dataset"] = ds["stats"]
    S.done(**{k: ds["stats"][k] for k in ("aligned_formats", "excluded_by_cause", "labeled_crops", "excluded_crops", "by_split_formats", "IE2_pct", "leakage")})

    # 10 --------------------------------------------------------------- fine-tuning
    S.start(10, "Ajustar TrOCR (early stopping con la mitad de selección)")
    try:
        ft = finetune(c, ds["items"], ds["snapshot_id"], log=log)
        tr = ft.metadata["training"]
        S.done(version=ft.version, steps=tr["steps"], best_step=tr["best_step"], select_cer_initial=_f(tr["initial_select_cer"]),
               select_cer_best=_f(tr["best_select_cer"]), train_seconds=round(tr["seconds"], 1), lora=bool(c.cfg["training"].get("lora")))
    except Exception as exc:  # noqa: BLE001
        S.done("error", error=str(exc))
        raise

    # 11 --------------------------------------------------------------- evaluation
    S.start(11, "Evaluar modelo base vs ajustado en el test congelado")
    ev = evaluate_models(c, [base.version, ft.version], split, gt, log=log)
    c.registry.attach(ft.version, "evaluation", ev)
    R["evaluation_initial"] = ev
    S.done(cer_base=_f(ev["models"][base.version]["cer"]), cer_ft=_f(ev["models"][ft.version]["cer"]), IE3_pct=_f(ev["relative_cer_reduction_pct"]),
           test_formats=ev["n_formats"])

    # 12 --------------------------------------------------------------- calibration, thresholds, IE4
    S.start(12, "Calibrar, fijar umbrales y medir IE4")
    prof = calibrate(c, ft.version, split, ds["items"], log=log)
    gates_ft = evaluate_gates(c, ft.version, base.version, gt, ds["items"], True, 0, log=log)
    prom_ft = promote(c, ft.version, "ing.ml.simulado", note="Activación del modelo ajustado (Sprint 3)")
    routing_ft = gates_ft["routing"]
    R["calibration_initial"] = prof
    R["routing_initial"] = routing_ft
    R["gates_initial"] = {"gates": gates_ft["gates"], "promotion": prom_ft}
    S.done(temperatures=prof["temperatures"], thresholds=prof["thresholds"], ece=_f(routing_ft["ece"]), ece_uncalibrated=_f(routing_ft["ece_uncalibrated"]),
           IE4_pct=_f(routing_ft["IE4_pct"]), residual_risk=_f(routing_ft["residual_risk"]), promoted=prom_ft["promoted"], failed_gates=prom_ft["failed"])

    # 13 --------------------------------------------------------------- operational batch + oracle validation
    S.start(13, "Validación humana simulada (oráculo) del lote operativo")
    active = c.registry.get_active()
    proc = c.processor()
    ops = [d for d in c.repo.list_documents(states=["CARGADO"]) if d.metadata.get("batch") == "operational"]
    rng = random.Random(int(c.cfg["seed"]) + 7)
    sup = man.get("supervisors", [])
    op_stats: Counter = Counter()
    op_records: list[dict[str, Any]] = []
    approvals: Counter = Counter()
    for d in ops:  # processed one by one so aliases learned in a validation help the next documents
        out = proc.process(d.id)
        op_stats[out.state] += 1
        if not out.run_id:
            continue
        values = c.repo.get_field_values(d.id)
        fid = d.metadata["form_id"]
        for v in values:
            truth = gt.value(fid, v.field_id)
            op_records.append({"route": v.route, "correct": v.value is not None and v.value == truth, "type": v.field_type})
        res = c.validation.confirm(d.id, oracle_decisions(c.validation, d.id, fid, gt), "validador.oraculo")
        op_stats["campos"] += res.stats["fields"]
        op_stats["tocados"] += res.touched
        op_stats["corregidos"] += res.corrected
        op_stats["alias"] += res.aliases
        dec = shift_boss_decision(rng, 0.15, sup)
        try:
            c.validation.approve(d.id, dec["decision"], dec["approver"], dec["comment"])
            approvals[dec["decision"]] += 1
            if dec["decision"] == "OBSERVADO":
                c.validation.confirm(d.id, {}, "validador.oraculo", accept_rest=True)
                c.validation.approve(d.id, "APROBADO", dec["approver"], "Conforme tras observación")
                approvals["APROBADO_tras_observacion"] += 1
        except ValidationBlocked as exc:
            approvals["bloqueado"] += 1
            log(f"        aprobación bloqueada: {exc}")
    auto = [r for r in op_records if r["route"] == "AUTO"]
    op_cov = len(auto) / len(op_records) if op_records else 0.0
    op_risk = float(np.mean([not r["correct"] for r in auto])) if auto else 0.0
    times_ops = form_times(str(c.workspace / "telemetry" / "events.jsonl"), active.version)
    R["operational"] = {"model": active.version, "states": dict(op_stats), "coverage": op_cov, "residual_risk": op_risk, "fields": len(op_records),
                        "touched": op_stats["tocados"], "corrected": op_stats["corregidos"], "aliases": op_stats["alias"], "times": times_ops}
    S.done(model=active.version, documents=len(ops), fields=len(op_records), auto=len(auto), coverage=_f(op_cov), residual_risk=_f(op_risk),
           touched=op_stats["tocados"], corrected=op_stats["corregidos"], aliases=op_stats["alias"], p50_s=_f(times_ops["p50_s"]), p95_s=_f(times_ops["p95_s"]))

    # 14 --------------------------------------------------------------- storage + approval + traceability
    S.start(14, "Almacenar con trazabilidad y aprobación simulada")
    validated_docs = [d for d in c.repo.list_documents() if c.repo.get_records(d.id)]
    traced = total = 0
    for d in validated_docs:
        rec = c.repo.get_records(d.id)[-1]
        for v in rec.values[:: max(1, len(rec.values) // 5)]:
            total += 1
            t = c.validation.trace(d.id, v.field_id)
            if t["documento"] and t["valor_propuesto"] and (t["recorte"] or t["valor_propuesto"]["texto"] is None):
                traced += 1
    final_states = Counter(d.state.value for d in c.repo.list_documents())
    R["storage"] = {"records": sum(len(c.repo.get_records(d.id)) for d in validated_docs), "traceability_pct": 100 * traced / total if total else None,
                    "approvals": dict(approvals), "states": dict(final_states)}
    S.done(**R["storage"])

    # 15 --------------------------------------------------------------- learning cycle
    S.start(15, "Ciclo de aprendizaje continuo (snapshot, replay, recalibración, G1–G6)")
    try:
        cyc = run_cycle(c, gt, log=log)
        if cyc.get("candidate") and cyc.get("status") == "pendiente_aprobacion":
            cyc["promotion"] = promote(c, cyc["candidate"], "jefe.planeamiento.simulado", note="Cierre de ciclo e2e")
            cyc["status"] = "promovido" if cyc["promotion"]["promoted"] else "bloqueado_por_compuertas"
        R["cycle"] = {k: v for k, v in cyc.items() if k not in ("evaluation",)}
        R["cycle_evaluation"] = cyc.get("evaluation")
        S.done(cyc.get("status", "ok"), candidate=cyc.get("candidate"), IE5_pct=_f(cyc.get("ie5_pct")), promoted=(cyc.get("promotion") or {}).get("promoted"),
               failed_gates=(cyc.get("promotion") or {}).get("failed"), corrections=cyc["stats"]["corrections"], replay=cyc["stats"]["replay"])
    except Exception as exc:  # noqa: BLE001
        R["cycle"] = {"status": "error", "error": str(exc)}
        S.done("error", error=str(exc))

    # 16 --------------------------------------------------------------- report
    S.start(16, "Generar reporte")
    R["steps"] = S.steps
    R["total_seconds"] = round(time.perf_counter() - t_all, 1)
    R["template"] = {"prioritized_fields": len({f.key for f in c.template.prioritized()}), "candidate_fields": len(c.template.field_keys()),
                     "instances": len(c.template.prioritized())}
    R["registry"] = [{"version": i.version, "status": i.status, "parent": i.parent} for i in c.registry.list()]
    R["active_model"] = c.registry.get_active().version
    paths = write_report(c, R, run_id)
    S.done(**paths)
    R["report_paths"] = paths
    R["total_seconds"] = round(time.perf_counter() - t_all, 1)
    Path(paths["json"]).write_text(json.dumps(R, indent=1, ensure_ascii=False, default=str))
    log(f"\nE2E completado en {R['total_seconds'] / 60:.1f} min · reporte {paths['markdown']}")
    return R
