"""CLI de Perforación IA (mensajes en español)."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import typer

from perfo.bootstrap import Container
from perfo.config import load_config

app = typer.Typer(help="Perforación IA: digitalización de formatos de perforación de taladros largos", no_args_is_help=True)
template_app = typer.Typer(help="Plantilla del formato", no_args_is_help=True)
app.add_typer(template_app, name="template")

ProfileOpt = typer.Option(None, "--profile", "-p", help="Perfil de configuración (smoke | full)")
WorkspaceOpt = typer.Option(None, "--workspace", "-w", help="Carpeta de trabajo (sobrescribe la del perfil)")


def container(profile: str | None, workspace: str | None = None, **overrides) -> Container:  # noqa: ANN003
    ov: dict = dict(overrides)
    if workspace:
        ov["workspace"] = workspace
    return Container(load_config(profile, ov))


@template_app.command("build")
def template_build(out: Path = typer.Option(Path("configs/template_v1.json"), help="Ruta del JSON de la plantilla")) -> None:
    """Genera la plantilla v1 y la matriz de priorización de campos (IE1)."""
    from perfo.synth.layout import build_template_v1, priority_matrix_rows  # noqa: PLC0415

    from perfo.synth.fonts import ensure_fonts  # noqa: PLC0415
    from perfo.synth.render import render_blank  # noqa: PLC0415

    tpl = build_template_v1()
    ref_path = out.with_name(out.stem + "_reference.png")
    tpl["reference_image"] = str(ref_path)
    printed = ensure_fonts(Path("data/fonts"))["printed"][0]
    render_blank(tpl, printed).convert("L").save(ref_path, optimize=True)
    out.write_text(json.dumps(tpl, indent=1, ensure_ascii=False))
    typer.echo(f"Imagen de referencia (formato vacío) en {ref_path}")
    rows = priority_matrix_rows()
    matrix = out.parent / "field_priority.csv"
    with matrix.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n_prio = sum(1 for r in rows if r["priorizado"] == "sí")
    typer.echo(f"Plantilla escrita en {out} ({len(rows)} campos candidatos, {n_prio} priorizados)")
    typer.echo(f"Matriz de priorización en {matrix}")


@template_app.command("show")
def template_show(profile: str | None = ProfileOpt) -> None:
    """Muestra un resumen de la plantilla vigente."""
    c = container(profile)
    t = c.template
    by_type = Counter(f.type.value for f in t.prioritized())
    typer.echo(f"{t.template_id} v{t.version} (vigente desde {t.valid_from}); referencia {t.width}×{t.height} px a {t.dpi} ppp")
    typer.echo(f"Instancias de campo priorizadas (celdas incluidas): {len(t.prioritized())}")
    typer.echo(f"Campos (conceptos) priorizados: {len({f.key for f in t.prioritized()})}")
    for k, v in sorted(by_type.items()):
        typer.echo(f"  {k:<11} {v}")


@app.command("synth")
def synth(
    profile: str = typer.Option("smoke", "--profile", "-p"),
    workspace: str | None = WorkspaceOpt,
    historical: int | None = typer.Option(None, help="Sobrescribe el número de formatos históricos"),
    operational: int | None = typer.Option(None, help="Sobrescribe el número de formatos operativos"),
    seed: int | None = typer.Option(None, help="Semilla (por defecto la de la configuración)"),
) -> None:
    """Genera el corpus sintético (imágenes, ground truth, CSV histórico y maestros)."""
    import time  # noqa: PLC0415

    from perfo.synth.generator import generate_corpus  # noqa: PLC0415

    c = container(profile, workspace)
    sc = dict(c.cfg["synth"])
    if historical is not None:
        sc["historical_forms"] = historical
    if operational is not None:
        sc["operational_forms"] = operational
    t0 = time.perf_counter()
    out = c.workspace / "synth"
    manifest = generate_corpus(sc, c.template_definition, out, c.cfg.resolve("data/fonts"), seed if seed is not None else int(c.cfg["seed"]))
    n_h = sum(1 for f in manifest["forms"] if f["batch"] == "historical")
    n_o = len(manifest["forms"]) - n_h
    typer.echo(f"Corpus sintético en {out} ({time.perf_counter() - t0:.1f} s)")
    typer.echo(f"  Formatos históricos: {n_h}; operativos: {n_o}; reescaneo duplicado: {'sí' if manifest['rescan'] else 'no'}")
    typer.echo(f"  Fuentes: {manifest['fonts_source']} ({len(manifest['fonts'])}); CSV histórico: {manifest['history_stats']['records']} registros")


masters_app = typer.Typer(help="Maestros versionados (labores, equipos, personal, códigos)", no_args_is_help=True)
app.add_typer(masters_app, name="masters")
doc_app = typer.Typer(help="Consulta de documentos", no_args_is_help=True)
app.add_typer(doc_app, name="doc")


@masters_app.command("import")
def masters_import(
    master_type: str = typer.Option(..., "--type", "-t", help="labores | equipos | personal | codigos"),
    file: Path = typer.Option(..., "--file", "-f", exists=True),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Importa un maestro desde CSV o Excel. Un archivo inválido se rechaza y se conserva la versión vigente."""
    c = container(profile, workspace)
    version, errors = c.masters.import_master(master_type, str(file))
    if errors:
        typer.secho(f"Maestro '{master_type}' rechazado ({len(errors)} errores); se conserva la versión vigente", fg="red")
        for e in errors[:20]:
            typer.echo(f"  - {e}")
        raise typer.Exit(1)
    typer.secho(f"Maestro '{master_type}' vigente: versión {version}", fg="green")


@masters_app.command("import-dir")
def masters_import_dir(directory: Path = typer.Argument(..., exists=True), profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> None:
    """Importa labores.csv, equipos.csv, personal.csv y codigos.csv desde una carpeta (CSV o XLSX)."""
    c = container(profile, workspace)
    for mtype in ("labores", "equipos", "personal", "codigos"):
        file = next((p for p in (directory / f"{mtype}.csv", directory / f"{mtype}.xlsx") if p.exists()), None)
        if file is None:
            typer.secho(f"  {mtype}: no encontrado en {directory}", fg="yellow")
            continue
        version, errors = c.masters.import_master(mtype, str(file))
        if errors:
            typer.secho(f"  {mtype}: rechazado ({'; '.join(errors[:3])})", fg="red")
        else:
            typer.echo(f"  {mtype}: versión {version} vigente ({len(c.masters.get_master(mtype).items)} registros)")


def _iter_inputs(c: Container, paths: list[Path], from_synth: str | None) -> list[tuple[bytes, dict]]:
    items: list[tuple[bytes, dict]] = []
    if from_synth:
        man = json.loads((c.workspace / "synth" / "manifest.json").read_text())
        for f in man["forms"]:
            if from_synth != "all" and f["batch"] != from_synth:
                continue
            p = c.workspace / "synth" / f["image"]
            items.append((p.read_bytes(), {"source_name": p.name, "uploaded_by": "secretaria.sim", "business_key": f["metadata_key"],
                                           "batch": f["batch"], "form_id": f["form_id"]}))
        if man.get("rescan") and from_synth in ("operational", "all"):
            p = c.workspace / "synth" / man["rescan"]["image"]
            src = next(f for f in man["forms"] if f["form_id"] == man["rescan"]["form_id"])
            items.append((p.read_bytes(), {"source_name": p.name, "uploaded_by": "secretaria.sim", "business_key": src["metadata_key"],
                                           "batch": "operational", "form_id": src["form_id"], "rescan": True}))
    for path in paths:
        files = sorted(x for x in path.rglob("*") if x.suffix.lower() in (".jpg", ".jpeg", ".png", ".pdf")) if path.is_dir() else [path]
        for fp in files:
            items.append((fp.read_bytes(), {"source_name": fp.name, "uploaded_by": "cli"}))
    return items


@app.command("ingest")
def ingest(
    paths: list[Path] = typer.Argument(None, help="Archivos o carpetas con escaneos (JPG, PNG o PDF)"),
    from_synth: str | None = typer.Option(None, "--from-synth", help="historical | operational | all (usa el manifiesto sintético)"),
    enqueue: bool = typer.Option(True, help="Encolar para procesamiento"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Registra formatos (hash SHA-256, duplicados e idempotencia) y los encola."""
    from perfo.application.ingest import IngestionRejected  # noqa: PLC0415

    c = container(profile, workspace)
    outcomes: Counter = Counter()
    for data, meta in _iter_inputs(c, paths or [], from_synth):
        try:
            doc_id = c.ingestion.submit(data, meta)
        except IngestionRejected as exc:
            typer.secho(f"  {meta['source_name']}: rechazado ({exc.code}) {exc}", fg="red")
            outcomes["rechazado"] += 1
            continue
        outcomes[c.ingestion.last_outcome] += 1
        if enqueue and c.ingestion.last_outcome == "nuevo":
            c.ingestion.enqueue(doc_id, meta["uploaded_by"])
    typer.echo("Ingesta: " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))


@app.command("process")
def process(
    doc_id: str | None = typer.Argument(None, help="Documento a procesar (por defecto, todos los que están en cola)"),
    until: str | None = typer.Option(None, help="Detenerse tras una etapa (segment)"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Procesa documentos en cola: preprocesamiento, segmentación, reconocimiento, reglas, homologación y enrutamiento."""
    c = container(profile, workspace)
    if until is None:
        c.ensure_baseline()
    proc = c.processor()
    ids = [doc_id] if doc_id else [d.id for d in c.repo.list_documents(states=["EN_COLA"])]
    for i, did in enumerate(ids, 1):
        out = proc.process(did, until=until)
        typer.echo(f"[{i}/{len(ids)}] {did[:8]} → {out.state} ({out.seconds:.1f} s) {out.cause or ''}")


@doc_app.command("show")
def doc_show(doc_id: str, profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt, all_fields: bool = typer.Option(False, "--all")) -> None:
    """Muestra estado, historial y campos reconocidos de un documento (acepta prefijo del id)."""
    from perfo.domain.routing import describe  # noqa: PLC0415

    c = container(profile, workspace)
    doc = _find_doc(c, doc_id)
    typer.echo(f"Documento {doc.id} · {doc.source_name} · estado {doc.state.value}")
    typer.echo(f"  Clave de negocio: {doc.business_key or '-'} · SHA-256 {doc.sha256[:16]}… · plantilla {doc.template_version or '-'}")
    if doc.state_cause:
        typer.echo(f"  Causa: {doc.state_cause}")
    for h in c.repo.state_history(doc.id):
        typer.echo(f"    {h['at'][:19]}  {h['from'] or '—':<19} → {h['to']:<19} {h['actor']}  {h['cause'] or ''}")
    values = c.repo.get_field_values(doc.id)
    if values:
        typer.echo(f"  Campos ({len(values)}; {sum(v.route == 'REVIEW' for v in values)} a revisión):")
        for v in values:
            if not all_fields and v.route != "REVIEW":
                continue
            conf = f"{v.confidence:.3f}" if v.confidence is not None else "  -  "
            reasons = "; ".join(describe(r) for r in v.reasons)
            typer.echo(f"    {v.route:<6} {v.field_id:<28} texto={v.text!r:<22} valor={v.value!r:<14} conf={conf} {reasons}")


def _find_doc(c: Container, prefix: str):  # noqa: ANN202
    doc = c.repo.get_document(prefix)
    if doc:
        return doc
    matches = [d for d in c.repo.list_documents() if d.id.startswith(prefix) or (d.metadata.get("form_id") == prefix)]
    if len(matches) != 1:
        raise typer.BadParameter(f"No se encontró un único documento para '{prefix}' ({len(matches)} coincidencias)")
    return matches[0]


evaluate_app = typer.Typer(help="Evaluación de modelos", no_args_is_help=True)
app.add_typer(evaluate_app, name="evaluate")


def _print_text_summary(title: str, s: dict) -> None:
    typer.echo(f"{title}: CER {s['cer']:.4f} · WER {s['wer']:.4f} · exactitud literal {s['exact']:.3f} (n={s['n']})")
    for t, v in s["by_type"].items():
        typer.echo(f"    {t:<11} CER {v['cer']:.4f}  exactos {v['exact']:.3f}  n={v['n']}")


@evaluate_app.command("preliminary")
def evaluate_preliminary(profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> dict:
    """CER inicial por tipo de campo y tiempos por formato sobre los documentos ya procesados (requiere ground truth sintético)."""
    from perfo.ml.evaluate import crop_items, field_accuracy, form_times, homologation_cases, homologation_eval, summarize_text, text_rows  # noqa: PLC0415
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415

    c = container(profile, workspace)
    gt = GroundTruth(c.workspace / "synth")
    active = c.registry.get_active()
    docs = [d.id for d in c.repo.list_documents() if d.metadata.get("form_id") and c.repo.get_field_values(d.id)]
    items = crop_items(c, docs, gt)
    recs = {r.crop_id: r for r in c.repo.get_recognitions([i.crop_id for i in items], active.version if active else None)}
    texts = [recs[i.crop_id].text if i.crop_id in recs else None for i in items]
    s = summarize_text(text_rows(items, texts))
    _print_text_summary(f"Modelo {active.version if active else '-'} sobre {len(docs)} formatos", s)
    fa = field_accuracy(c, items, texts, gt)
    typer.echo(f"Exactitud por campo (valor tipado/homologado): {fa['field_accuracy']:.3f} (n={fa['n']})")
    h_lit = homologation_eval(c.homologator, homologation_cases(c, items))
    h_rec = homologation_eval(c.homologator, homologation_cases(c, items, texts))
    typer.echo(f"Homologación sobre texto literal: Exactitud@1 {h_lit['acc_at_1']:.3f} · Recall@3 {h_lit['recall_at_3']:.3f} · auto {h_lit['auto_resolved']:.3f} (n={h_lit['n']})")
    typer.echo(f"Homologación sobre texto reconocido: Exactitud@1 {h_rec['acc_at_1']:.3f} · Recall@3 {h_rec['recall_at_3']:.3f} (n={h_rec['n']})")
    t = form_times(str(c.workspace / "telemetry" / "events.jsonl"), active.version if active else None)
    typer.echo(f"Tiempo por formato: p50 {t['p50_s']:.2f} s · p95 {t['p95_s']:.2f} s (n={t['n']})")
    return {"text": s, "field_accuracy": fa, "homologation_literal": h_lit, "homologation_recognized": h_rec, "times": t}


dataset_app = typer.Typer(help="Dataset por supervisión débil", no_args_is_help=True)
app.add_typer(dataset_app, name="dataset")


def _history_csv(c: Container, history: Path | None) -> Path:
    if history:
        return history
    p = c.workspace / "synth" / "history" / "registro_historico.csv"
    if not p.exists():
        raise typer.BadParameter("Indique --history con el CSV histórico exportado")
    return p


@dataset_app.command("build")
def dataset_build(
    history: Path | None = typer.Option(None, "--history", help="CSV histórico (registro_id, fecha, guardia, equipo, campo, valor)"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Alinea los formatos con el histórico (fecha, guardia, equipo), deriva etiquetas literales, particiona y congela el test."""
    from perfo.ml.pipeline import build_dataset  # noqa: PLC0415

    c = container(profile, workspace)
    res = build_dataset(c, _history_csv(c, history), log=typer.echo)
    typer.echo(json.dumps(res["stats"]["by_split_formats"], ensure_ascii=False))


@app.command("train")
def train_cmd(
    lora: bool = typer.Option(False, "--lora", help="Ajuste con LoRA en lugar de ajuste completo"),
    max_steps: int | None = typer.Option(None, help="Sobrescribe training.max_steps"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Ajusta TrOCR con el dataset propio (early stopping con la mitad de selección). Registra un candidato."""
    from perfo.ml.pipeline import finetune, latest_initial_snapshot, load_snapshot_items  # noqa: PLC0415

    c = container(profile, workspace)
    snap = latest_initial_snapshot(c)
    if snap is None:
        raise typer.BadParameter("No hay dataset; ejecute 'perfo dataset build'")
    cfg = dict(c.cfg["training"])
    cfg["lora"] = lora or bool(cfg.get("lora"))
    if max_steps:
        cfg["max_steps"] = max_steps
    info = finetune(c, load_snapshot_items(c, snap), snap, train_cfg=cfg, log=typer.echo)
    typer.secho(f"Candidato registrado: {info.version} (sha256 {info.artifact_sha256[:12]}…)", fg="green")


@evaluate_app.command("models")
def evaluate_models_cmd(
    versions: list[str] = typer.Argument(None, help="Versiones a comparar (por defecto: línea base y último candidato)"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Compara modelos en el conjunto de prueba congelado: CER, WER, exactitud por campo y por tipo, operadores no vistos."""
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415
    from perfo.ml.pipeline import SPLIT_VERSION, baseline_version, evaluate_models  # noqa: PLC0415

    c = container(profile, workspace)
    split = c.repo.get_split(SPLIT_VERSION)
    if not versions:
        cands = [i.version for i in c.registry.list() if i.parent]
        versions = [baseline_version(c)] + cands[-1:]
    res = evaluate_models(c, versions, split, GroundTruth(c.workspace / "synth"), log=typer.echo)
    if "relative_cer_reduction_pct" in res:
        typer.secho(f"Reducción relativa de CER ({versions[0]} → {versions[1]}): {res['relative_cer_reduction_pct']:.1f} %", fg="cyan")
    c.registry.attach(versions[-1], "evaluation", res)


@app.command("calibrate")
def calibrate_cmd(version: str | None = typer.Argument(None), profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> None:
    """Escalado de temperatura por tipo de campo y umbral en la curva cobertura–riesgo (mitad de calibración)."""
    from perfo.ml.pipeline import SPLIT_VERSION, calibrate, latest_initial_snapshot, load_snapshot_items  # noqa: PLC0415

    c = container(profile, workspace)
    version = version or [i.version for i in c.registry.list() if i.parent][-1]
    calibrate(c, version, c.repo.get_split(SPLIT_VERSION), load_snapshot_items(c, latest_initial_snapshot(c)), log=typer.echo)


@evaluate_app.command("routing")
def evaluate_routing_cmd(version: str | None = typer.Argument(None), profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> None:
    """ECE, curva cobertura–riesgo e IE4 en el conjunto de prueba congelado."""
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415
    from perfo.ml.pipeline import SPLIT_VERSION, evaluate_routing  # noqa: PLC0415

    c = container(profile, workspace)
    version = version or [i.version for i in c.registry.list() if i.parent][-1]
    res = evaluate_routing(c, version, c.repo.get_split(SPLIT_VERSION)["test"], GroundTruth(c.workspace / "synth"), log=typer.echo)
    for t, v in res["by_type"].items():
        typer.echo(f"    {t:<11} cobertura {v['coverage']:.3f} ({v['auto']}/{v['n']}) · riesgo {v['risk']:.4f} · umbral {v['threshold']}")
    typer.echo(f"  Motivos de revisión: {res['review_reasons']}")
    c.registry.attach(version, "routing_test", {k: v for k, v in res.items() if k not in ("curve",)})


@app.command("status")
def status(profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> None:
    """Resumen de documentos por estado y del modelo activo."""
    c = container(profile, workspace)
    docs = c.repo.list_documents()
    counts = Counter(d.state.value for d in docs)
    typer.echo(f"Espacio de trabajo: {c.workspace}")
    typer.echo(f"Documentos: {len(docs)}")
    for st, n in sorted(counts.items()):
        typer.echo(f"  {st:<20} {n}")
    active = c.registry.get_active()
    typer.echo(f"Modelo activo: {active.version if active else '(ninguno)'}")


if __name__ == "__main__":
    app()
