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
ReferenceOpt = typer.Option(None, "--reference", help="CSV de referencia por doble digitación (datos reales); por defecto, el ground truth sintético")
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


@template_app.command("import-hu06")
def template_import_hu06(
    hu06_json: Path = typer.Argument(..., exists=True, help="plantilla_v1_reporte-perforacion-tl.json de HU06"),
    reference_image: Path = typer.Option(..., "--reference-image", exists=True, help="Página plana de referencia (referencia_plana.png)"),
    out: Path = typer.Option(Path("configs/template_v2.json"), help="Plantilla de salida"),
    codes_out: Path = typer.Option(Path("configs/masters_fisico/codigos.csv"), help="Maestro de códigos tomado de la leyenda del formato"),
) -> None:
    """Convierte la plantilla del formato físico medida en HU06 (página plana) a una plantilla de perfo."""
    import cv2  # noqa: PLC0415

    from perfo.adapters.hu06.template import convert, frame_anchors, legend_rows  # noqa: PLC0415
    from perfo.domain.fields import parse_template  # noqa: PLC0415

    ref_out = out.with_name(out.stem + "_reference.png")
    gray = cv2.imread(str(reference_image), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise typer.BadParameter(f"No se pudo leer la imagen de referencia {reference_image}")
    definition = convert(json.loads(hu06_json.read_text(encoding="utf-8")), str(ref_out), anchors_px=frame_anchors(gray))
    if gray.shape[::-1] != (definition["reference"]["width"], definition["reference"]["height"]):
        raise typer.BadParameter(f"La imagen mide {gray.shape[1]}×{gray.shape[0]} px y la plantilla declara "
                                 f"{definition['reference']['width']}×{definition['reference']['height']}")
    cv2.imwrite(str(ref_out), gray)
    out.write_text(json.dumps(definition, indent=1, ensure_ascii=False))
    codes_out.parent.mkdir(parents=True, exist_ok=True)
    with codes_out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["tipo", "codigo", "descripcion"])
        w.writeheader()
        w.writerows(legend_rows())
    t = parse_template(definition)
    by_type = Counter(f.type.value for f in t.prioritized())
    typer.echo(f"Plantilla {t.template_id} v{t.version} en {out} (referencia {ref_out}, {t.width}×{t.height} px)")
    typer.echo(f"  Conceptos priorizados: {len(t.field_keys())}; instancias (celdas incluidas): {len(t.prioritized())}; "
               + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())))
    typer.echo(f"  Anclas de alineación (esquinas de los marcos impresos): {len(definition['anchors'])}")
    typer.echo(f"Maestro de códigos de la leyenda en {codes_out}")


@template_app.command("from-photos")
def template_from_photos(
    photos: Path = typer.Argument(..., exists=True, file_okay=False, help="Carpeta con las fotos reales (Formatos_fisicos)"),
    base: Path = typer.Option(Path("configs/template_v2.json"), exists=True, help="Plantilla que aporta los conjuntos de códigos"),
    out: Path = typer.Option(Path("configs/templates_real"), help="Carpeta de las definiciones"),
    images: Path = typer.Option(Path("data/plantillas_reales"), help="Carpeta de las referencias planas; vienen de fotos reales y quedan fuera de git"),
) -> None:
    """Construye las plantillas de los formatos reales (taladros largos y Jackleg) a partir de sus fotos."""
    from perfo.adapters.hu06.real_forms import build  # noqa: PLC0415

    for path in build(photos, json.loads(base.read_text(encoding="utf-8")), out, images, str(images)):
        typer.echo(f"Escrito {path}")


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


def _iter_inputs(c: Container, paths: list[Path], from_synth: str | None, metadata_csv: Path | None = None,
                 batch: str = "historical", user: str = "cli") -> list[tuple[bytes, dict]]:
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
    meta_by_file: dict[str, dict] = {}
    if metadata_csv:
        from perfo.domain.parsing import parse_date  # noqa: PLC0415

        with open(metadata_csv, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                d = parse_date(row["fecha"])
                key = f"{d.value if d.ok else row['fecha']}|{row['guardia'].strip().upper()}|{row['equipo'].strip()}"
                meta_by_file[row["archivo"].strip()] = {"business_key": key, **({"batch": row["lote"]} if row.get("lote") else {})}
    for path in paths:
        files = sorted(x for x in path.rglob("*") if x.suffix.lower() in (".jpg", ".jpeg", ".png", ".pdf")) if path.is_dir() else [path]
        for fp in files:
            meta = {"source_name": fp.name, "uploaded_by": user, "form_id": fp.name, "batch": batch, **meta_by_file.get(fp.name, {})}
            items.append((fp.read_bytes(), meta))
    return items


@app.command("ingest")
def ingest(
    paths: list[Path] = typer.Argument(None, help="Archivos o carpetas con escaneos (JPG, PNG o PDF)"),
    from_synth: str | None = typer.Option(None, "--from-synth", help="historical | operational | all (usa el manifiesto sintético)"),
    metadata: Path | None = typer.Option(None, "--metadata", help="CSV con archivo, fecha, guardia, equipo[, lote] para escaneos reales"),
    batch: str = typer.Option("historical", "--batch", help="Lote de los escaneos reales: historical | operational"),
    user: str = typer.Option("cli", "--user", "-u", help="Usuario que carga"),
    enqueue: bool = typer.Option(True, help="Encolar para procesamiento"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Registra formatos (hash SHA-256, duplicados e idempotencia) y los encola."""
    from perfo.application.ingest import IngestionRejected  # noqa: PLC0415

    c = container(profile, workspace)
    outcomes: Counter = Counter()
    for data, meta in _iter_inputs(c, paths or [], from_synth, metadata, batch, user):
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
    model: str | None = typer.Option(None, "--model", help="Versión de modelo (por defecto, la activa; latest = el último modelo ajustado registrado)"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Procesa documentos en cola: preprocesamiento, segmentación, reconocimiento, reglas, homologación y enrutamiento."""
    c = container(profile, workspace)
    if until is None:
        c.ensure_baseline()
    if model == "latest":
        trained = [i for i in c.registry.list() if not i.version.startswith("baseline-")]
        model = max(trained, key=lambda i: i.created_at).version if trained else None
        typer.echo(f"Modelo: {model or 'activo'} ({'último ajustado registrado' if model else 'no hay modelos ajustados'})")
    proc = c.processor(version=model)
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


def _gt(c: Container, reference: Path | None):  # noqa: ANN202
    from perfo.ml.groundtruth import load_ground_truth  # noqa: PLC0415

    return load_ground_truth(c.workspace, reference)


def _find_doc(c: Container, prefix: str):  # noqa: ANN202
    doc = c.repo.get_document(prefix)
    if doc:
        return doc
    matches = [d for d in c.repo.list_documents() if d.id.startswith(prefix) or (d.metadata.get("form_id") == prefix)]
    if len(matches) > 1:  # a re-scan shares the form id: prefer the active document
        active = [d for d in matches if d.state.value != "DUPLICADO_PENDIENTE"]
        matches = active if len(active) == 1 else matches
    if len(matches) != 1:
        raise typer.BadParameter(f"No se encontró un único documento para '{prefix}' ({len(matches)} coincidencias)")
    return matches[0]


evaluate_app = typer.Typer(help="Evaluación de modelos", no_args_is_help=True)
app.add_typer(evaluate_app, name="evaluate")
truth_app = typer.Typer(help="Verdad de referencia (doble digitación o anotaciones)", no_args_is_help=True)
app.add_typer(truth_app, name="truth")


@truth_app.command("import-boxes")
def truth_import_boxes(
    truth_dir: Path = typer.Argument(..., exists=True, file_okay=False, help="Carpeta con un JSON por formato: [{caja, texto, tachado}]"),
    images: Path = typer.Option(..., "--images", exists=True, file_okay=False, help="Carpeta de las imágenes (da el nombre de archivo de cada JSON)"),
    out: Path = typer.Option(..., "--out", help="CSV de referencia: archivo, campo, valor, texto, operador"),
    min_overlap: float = typer.Option(0.5, help="Fracción mínima de la caja del texto dentro del campo"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Convierte anotaciones por cajas sobre la página plana (verdad de HU06) en el CSV de referencia por campo."""
    from perfo.adapters.hu06.truth import boxes_to_reference  # noqa: PLC0415
    from perfo.application.structuring import effective_code_sets  # noqa: PLC0415

    c = container(profile, workspace)
    files = {p.stem: p.name for p in images.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")}
    try:
        codes = c.masters.get_master("codigos").items
    except Exception:  # noqa: BLE001 (codes master not imported yet: the template values are used)
        codes = None
    rows, stats = boxes_to_reference(c.template, truth_dir, files, effective_code_sets(c.template, codes), min_overlap)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["archivo", "campo", "valor", "texto", "operador"])
        w.writeheader()
        w.writerows(rows)
    typer.echo(f"Referencia en {out}: {len(rows)} campos de {stats.get('formatos', 0)} formatos")
    typer.echo("  " + ", ".join(f"{k}={v}" for k, v in stats.items() if k != "sin_imagen"))
    if stats.get("sin_imagen"):
        typer.secho(f"  Sin imagen para: {', '.join(stats['sin_imagen'][:10])}", fg="yellow")


@evaluate_app.command("reference")
def evaluate_reference_cmd(
    versions: list[str] = typer.Argument(None, help="Versiones a comparar (por defecto: línea base y las dos últimas registradas)"),
    reference: Path = typer.Option(..., "--reference", exists=True, help="CSV de referencia (archivo, campo, valor, texto)"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Evalúa modelos sobre todos los documentos procesados que tienen verdad de referencia (sin partición congelada)."""
    from perfo.ml.pipeline import baseline_version, evaluate_models  # noqa: PLC0415

    c = container(profile, workspace)
    gt = _gt(c, reference)
    docs = c.repo.list_documents()
    states = Counter(d.state.value for d in docs)
    with_truth = [d.id for d in docs if c.repo.get_crops(d.id) and gt.fields(d.metadata.get("form_id") or d.source_name)]
    typer.echo(f"Documentos: {len(docs)} ({', '.join(f'{k} {v}' for k, v in sorted(states.items()))}); segmentados con verdad: {len(with_truth)}")
    for d in docs:
        if d.state.value in ("ERROR", "EN_REVISION") and not c.repo.get_crops(d.id):
            typer.echo(f"  {d.source_name}: {d.state.value} · {d.state_cause}")
    if not with_truth:
        raise typer.BadParameter("No hay documentos segmentados cuyo nombre de archivo figure en la referencia")
    if not versions:  # line base against the most recent registered versions
        base = baseline_version(c)
        versions = [base] + [i.version for i in c.registry.list() if i.version != base and c.has_weights(i.version)][-2:]
    res = evaluate_models(c, versions, {"test": with_truth}, gt, log=lambda *_: None)
    for v in versions:
        m = res["models"][v]
        typer.secho(f"{v}: CER {m['cer']:.4f} · WER {m['wer']:.4f} · exactitud literal {m['exact']:.3f} · "
                    f"exactitud por campo {m['field_accuracy']:.3f} (n={m['n']})", fg="cyan")
        for t, s in m["by_type"].items():
            fa = m["field_accuracy_by_type"].get(t, {})
            typer.echo(f"    {t:<11} CER {s['cer']:.4f}  exactos {s['exact']:.3f}  valor {fa.get('accuracy', float('nan')):.3f}  n={s['n']}")
    if "relative_cer_reduction_pct" in res:
        typer.secho(f"Reducción relativa de CER ({versions[0]} → {versions[1]}): {res['relative_cer_reduction_pct']:.1f} %", fg="cyan")
    out = c.workspace / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"evaluacion_referencia_{Path(reference).stem}.json"
    if path.exists():  # a version not evaluated this time keeps its measurement: it is the history of the model
        res["models"] = {**json.loads(path.read_text()).get("models", {}), **res["models"]}
    path.write_text(json.dumps({"reference": str(reference), "documents": len(with_truth), "states": dict(states), **res},
                               indent=1, ensure_ascii=False, default=str))
    typer.echo(f"Detalle por campo en {path}")


def _print_text_summary(title: str, s: dict) -> None:
    typer.echo(f"{title}: CER {s['cer']:.4f} · WER {s['wer']:.4f} · exactitud literal {s['exact']:.3f} (n={s['n']})")
    for t, v in s["by_type"].items():
        typer.echo(f"    {t:<11} CER {v['cer']:.4f}  exactos {v['exact']:.3f}  n={v['n']}")


@evaluate_app.command("preliminary")
def evaluate_preliminary(profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt, reference: Path | None = ReferenceOpt) -> dict:
    """CER inicial por tipo de campo y tiempos por formato sobre los documentos ya procesados (requiere ground truth sintético)."""
    from perfo.ml.evaluate import crop_items, field_accuracy, form_times, homologation_cases, homologation_eval, summarize_text, text_rows  # noqa: PLC0415
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415

    c = container(profile, workspace)
    gt = _gt(c, reference)
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
    workspace: str | None = WorkspaceOpt, reference: Path | None = ReferenceOpt) -> None:
    """Compara modelos en el conjunto de prueba congelado: CER, WER, exactitud por campo y por tipo, operadores no vistos."""
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415
    from perfo.ml.pipeline import SPLIT_VERSION, baseline_version, evaluate_models  # noqa: PLC0415

    c = container(profile, workspace)
    split = c.repo.get_split(SPLIT_VERSION)
    if not versions:
        cands = [i.version for i in c.registry.list() if i.parent]
        versions = [baseline_version(c)] + cands[-1:]
    res = evaluate_models(c, versions, split, _gt(c, reference), log=typer.echo)
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
def evaluate_routing_cmd(
    version: str | None = typer.Argument(None),
    plots: bool = typer.Option(True, help="Genera los gráficos PNG"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt, reference: Path | None = ReferenceOpt) -> None:
    """ECE, curva cobertura–riesgo e IE4 en el conjunto de prueba congelado."""
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415
    from perfo.ml.pipeline import SPLIT_VERSION, evaluate_routing  # noqa: PLC0415

    c = container(profile, workspace)
    version = version or [i.version for i in c.registry.list() if i.parent][-1]
    res = evaluate_routing(c, version, c.repo.get_split(SPLIT_VERSION)["test"], _gt(c, reference), log=typer.echo)
    for t, v in res["by_type"].items():
        typer.echo(f"    {t:<11} cobertura {v['coverage']:.3f} ({v['auto']}/{v['n']}) · riesgo {v['risk']:.4f} · umbral {v['threshold']}")
    typer.echo(f"  Motivos de revisión: {res['review_reasons']}")
    c.registry.attach(version, "routing_test", res)
    if plots:
        from perfo.reporting.plots import coverage_risk_plot, reliability_plot  # noqa: PLC0415

        out = c.workspace / "reports"
        out.mkdir(exist_ok=True)
        typer.echo(f"  Gráficos: {coverage_risk_plot(res, float(c.cfg['thresholds']['risk_target']), out / f'cobertura_riesgo_{version}.png')}")
        typer.echo(f"            {reliability_plot(res, out / f'fiabilidad_{version}.png')}")


review_app = typer.Typer(help="Revisión manual, validación, aprobación y trazabilidad", no_args_is_help=True)
app.add_typer(review_app, name="review")


def _kv(pairs: list[str] | None, opt: str) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise typer.BadParameter(f"{opt} espera campo=valor, recibió '{p}'")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@review_app.command("list")
def review_list(profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> None:
    """Documentos en EN_REVISION con el número de campos que requieren acción."""
    c = container(profile, workspace)
    docs = c.repo.list_documents(states=["EN_REVISION"])
    typer.echo(f"{len(docs)} documentos en revisión")
    for d in docs:
        vals = c.repo.get_field_values(d.id)
        n_rev = sum(1 for v in vals if v.route == "REVIEW")
        typer.echo(f"  {d.id[:8]}  {d.metadata.get('form_id', d.source_name):<10} {d.business_key or '-':<24} {n_rev:>3}/{len(vals):<3} a revisar  {d.state_cause or ''}")


@review_app.command("show")
def review_show(
    doc_id: str,
    sheet: bool = typer.Option(True, help="Genera una hoja PNG con los recortes a revisar"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Campos que requieren acción, con lectura, propuesta, confianza, motivos y candidatas."""
    from types import SimpleNamespace  # noqa: PLC0415

    from perfo.domain.routing import describe  # noqa: PLC0415
    from perfo.reporting.review_sheet import review_sheet  # noqa: PLC0415

    c = container(profile, workspace)
    doc = _find_doc(c, doc_id)
    pend = c.validation.pending(doc.id)
    typer.echo(f"Documento {doc.id[:8]} ({doc.metadata.get('form_id', doc.source_name)}) · {doc.state.value} · {len(pend)} campos requieren acción")
    rows = []
    for i, v in enumerate(pend, 1):
        conf = f"{v.confidence:.3f}" if v.confidence is not None else "  —  "
        typer.echo(f"  {i:>2}. {v.field_id:<28} leído={v.text!r:<20} propuesto={v.value!r:<12} conf={conf}  {'; '.join(describe(r) for r in v.reasons)}")
        if v.homologation and v.homologation.candidates:
            typer.echo("        candidatas: " + " | ".join(f"{x['code']} {x['description']} ({x['score']:.2f})" for x in v.homologation.candidates[:3]))
        crop = c.repo.get_crop(v.crop_id) if v.crop_id else None
        rows.append(SimpleNamespace(field_id=v.field_id, text=v.text, value=v.value, confidence=v.confidence, reasons=v.reasons, mark=v.mark,
                                    crop_key=crop.object_key if crop else None,
                                    candidates=v.homologation.candidates if v.homologation else []))
    if sheet and rows:
        out = c.workspace / "review"
        out.mkdir(exist_ok=True)
        path = review_sheet(c.storage, rows, out / f"{doc.metadata.get('form_id', doc.id[:8])}.png", f"Revisión {doc.metadata.get('form_id', '')} · {doc.business_key}")
        typer.echo(f"Hoja de revisión: {path}")
        if doc.preprocessed_key:
            typer.echo(f"Imagen preprocesada: {c.storage.local_path(doc.preprocessed_key)}")
    typer.echo("Para confirmar: perfo review confirm <doc> --set campo=valor --choose campo=CÓDIGO --clear campo --accept-rest --user <nombre>")


@review_app.command("confirm")
def review_confirm(
    doc_id: str,
    sets: list[str] = typer.Option(None, "--set", help="Corrige el valor: campo=valor (para maestros, el código)"),
    texts: list[str] = typer.Option(None, "--text", help="Transcripción literal corregida: campo=texto"),
    chooses: list[str] = typer.Option(None, "--choose", help="Elige una candidata de homologación: campo=CÓDIGO (genera alias)"),
    clears: list[str] = typer.Option(None, "--clear", help="Marca el campo como vacío"),
    accept_rest: bool = typer.Option(False, "--accept-rest", help="Confirma explícitamente como propuestos los campos restantes"),
    user: str = typer.Option(..., "--user", "-u", help="Usuario que valida"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Valida el documento: aplica correcciones, genera alias y guarda una versión inmutable del registro."""
    from perfo.application.validation import Decision, ValidationBlocked  # noqa: PLC0415

    c = container(profile, workspace)
    doc = _find_doc(c, doc_id)
    decisions: dict[str, Decision] = {}
    text_map = _kv(texts, "--text")
    for k, v in _kv(sets, "--set").items():
        decisions[k] = Decision("correct", value=v, text=text_map.pop(k, None))
    for k, v in text_map.items():
        decisions[k] = Decision("correct", text=v)
    for k, v in _kv(chooses, "--choose").items():
        decisions[k] = Decision("choose", value=v)
    for k in clears or []:
        decisions[k] = Decision("clear")
    try:
        res = c.validation.confirm(doc.id, decisions, user, accept_rest=accept_rest)
    except ValidationBlocked as exc:
        typer.secho(f"No se puede confirmar: {exc}", fg="red")
        for f in exc.fields[:30]:
            typer.echo(f"  - {f}")
        raise typer.Exit(1) from exc
    typer.secho(f"Registro v{res.record.version} guardado (sha256 {res.record.sha256[:12]}…): {res.stats['auto_accepted']} automáticos, "
                f"{res.touched} revisados, {res.corrected} corregidos, {res.aliases} alias aprendidos", fg="green")


@review_app.command("oracle")
def review_oracle(
    batch: str | None = typer.Option(None, help="Limita a un lote (historical | operational)"),
    approve: bool = typer.Option(True, help="Simula además la aprobación del Jefe de Guardia"),
    observe_rate: float = typer.Option(0.15),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt, reference: Path | None = ReferenceOpt) -> None:
    """Validación humana simulada con el ground truth sintético (solo toca campos enrutados a revisión)."""
    import random  # noqa: PLC0415

    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415
    from perfo.synth.oracle import oracle_decisions, shift_boss_decision  # noqa: PLC0415

    c = container(profile, workspace)
    gt = _gt(c, reference)
    rng = random.Random(int(c.cfg["seed"]) + 7)
    sup = gt.manifest.get("supervisors", [])
    tot = Counter()
    for d in c.repo.list_documents(states=["EN_REVISION"], **({"batch": batch} if batch else {})):
        if not c.repo.get_field_values(d.id):
            continue
        res = c.validation.confirm(d.id, oracle_decisions(c.validation, d.id, d.metadata["form_id"], gt), "validador.oraculo")
        tot.update({"documentos": 1, "campos": res.stats["fields"], "revisados": res.touched, "corregidos": res.corrected, "alias": res.aliases})
        if approve:
            dec = shift_boss_decision(rng, observe_rate, sup)
            c.validation.approve(d.id, dec["decision"], dec["approver"], dec["comment"])
            tot[dec["decision"].lower()] += 1
            if dec["decision"] == "OBSERVADO":
                c.validation.confirm(d.id, oracle_decisions(c.validation, d.id, d.metadata["form_id"], gt), "validador.oraculo", accept_rest=True)
                c.validation.approve(d.id, "APROBADO", dec["approver"], "Conforme tras observación")
    typer.echo("Validación simulada: " + ", ".join(f"{k}={v}" for k, v in tot.items()))


@app.command("approve")
def approve_cmd(
    doc_id: str,
    decision: str = typer.Option(..., "--decision", "-d", help="aprobar | observar"),
    comment: str = typer.Option("", "--comment"),
    user: str = typer.Option(..., "--user", "-u"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Conformidad u observación del Jefe de Guardia sobre un registro validado (HU11)."""
    c = container(profile, workspace)
    doc = _find_doc(c, doc_id)
    dec = {"aprobar": "APROBADO", "observar": "OBSERVADO"}.get(decision.lower(), decision.upper())
    ap = c.validation.approve(doc.id, dec, user, comment)
    typer.secho(f"{ap.decision} por {ap.approver}" + (f": {ap.comment}" if ap.comment else ""), fg="green")


@review_app.command("tray")
def review_tray(
    fecha: str | None = typer.Option(None), guardia: str | None = typer.Option(None), equipo: str | None = typer.Option(None),
    profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt,
) -> None:
    """Bandeja de registros confirmados pendientes de aprobación (HU10)."""
    c = container(profile, workspace)
    rows = c.validation.tray(**{k: v for k, v in {"fecha": fecha, "guardia": guardia, "equipo": equipo}.items() if v})
    typer.echo(f"{len(rows)} registros pendientes de aprobación")
    for r in rows:
        typer.echo(f"  {r['document_id'][:8]}  {r['fecha']}  guardia {r['guardia']}  {r['equipo']:<6} operador {r['operador'] or '-':<6} v{r['version']}  corregidos {r['corregidos']}")


@app.command("trace")
def trace_cmd(doc_id: str, field_id: str, profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> None:
    """Trazabilidad de un valor almacenado hasta el recorte y el documento original (HU09)."""
    c = container(profile, workspace)
    doc = _find_doc(c, doc_id)
    typer.echo(json.dumps(c.validation.trace(doc.id, field_id), indent=2, ensure_ascii=False, default=str))


cycle_app = typer.Typer(help="Aprendizaje continuo", no_args_is_help=True)
app.add_typer(cycle_app, name="cycle")
registry_app = typer.Typer(help="Registro de modelos", no_args_is_help=True)
app.add_typer(registry_app, name="registry")


def _print_gates(gates: list[dict]) -> None:
    for g in gates:
        typer.secho(f"    {g['gate']} {g['name']:<13} {'✔' if g['passed'] else '✘'}  {g['detail']}", fg="green" if g["passed"] else "red")


@cycle_app.command("run")
def cycle_run(profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt, reference: Path | None = ReferenceOpt) -> None:
    """Cierre de ciclo: snapshot de correcciones, reentrenamiento con replay, recalibración y compuertas G1–G5."""
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415
    from perfo.ml.learning_cycle import run_cycle  # noqa: PLC0415

    c = container(profile, workspace)
    res = run_cycle(c, _gt(c, reference), log=typer.echo)
    typer.echo(f"Estado del ciclo: {res['status']}")
    if res.get("candidate"):
        typer.echo(f"Candidato {res['candidate']} · IE5 = {res['ie5_pct']:.1f} % · promover con: perfo promote {res['candidate']} --approver <nombre>")


@app.command("gates")
def gates_cmd(version: str, profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt, reference: Path | None = ReferenceOpt) -> None:
    """Evalúa las compuertas de un candidato frente al modelo activo (p. ej., el primer modelo ajustado)."""
    from perfo.ml.groundtruth import GroundTruth  # noqa: PLC0415
    from perfo.ml.learning_cycle import evaluate_gates  # noqa: PLC0415
    from perfo.ml.pipeline import latest_initial_snapshot, load_snapshot_items  # noqa: PLC0415

    c = container(profile, workspace)
    active = c.registry.get_active() or c.ensure_baseline()
    info = c.registry.get(version)
    items = load_snapshot_items(c, info.metadata.get("dataset_snapshot") or latest_initial_snapshot(c))
    ok = all(i.sha256 and c.storage.exists(i.object_key) for i in items)
    evaluate_gates(c, version, active.version, _gt(c, reference), items, ok, 0, log=typer.echo)


@app.command("promote")
def promote_cmd(
    version: str,
    approver: str = typer.Option(..., "--approver", "-a", help="Rol autorizado que aprueba la promoción (G6)"),
    note: str = typer.Option("", "--note"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Promoción explícita (G6): registra quién aprobó, la fecha, la versión y la evidencia. Se rechaza si falla una compuerta."""
    from perfo.ml.learning_cycle import promote  # noqa: PLC0415

    c = container(profile, workspace)
    res = promote(c, version, approver, note=note)
    if res["promoted"]:
        typer.secho(f"Promovido {version} (antes {res['previous']}); evidencia {res['evidence']}", fg="green")
    else:
        typer.secho(f"Promoción rechazada: compuertas fallidas {res['failed']}; el modelo activo sigue siendo {res['active']}", fg="red")
        raise typer.Exit(1)


@registry_app.command("import")
def registry_import(
    version: str | None = typer.Argument(None, help="Versión a importar (por defecto, la activa del origen)"),
    from_workspace: Path | None = typer.Option(None, "--from-workspace", exists=True, file_okay=False,
                                               help="Espacio de trabajo de origen (por defecto, la última corrida e2e smoke)"),
    profile: str | None = ProfileOpt,
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Copia el artefacto de un modelo de otro espacio de trabajo como candidato, sin su calibración.

    La calibración depende de los datos donde se midió; en un formato distinto se vuelve a calibrar.
    """
    from perfo.adapters.registry_disk.registry import DiskModelRegistry  # noqa: PLC0415

    c = container(profile, workspace)
    if from_workspace is None:
        latest = _latest_e2e_workspace("smoke")
        if latest is None:
            raise typer.BadParameter("No hay corridas e2e smoke; indique --from-workspace")
        from_workspace = Path(latest)
    source = DiskModelRegistry(from_workspace / "registry")
    if version is None:
        active = source.get_active()
        if active is None:
            raise typer.BadParameter(f"{from_workspace} no tiene modelo activo; indique la versión")
        version = active.version
    try:
        c.registry.get(version)
        typer.echo(f"{version} ya está registrado en {c.workspace}; no se copia otra vez")
        return
    except KeyError:
        pass
    src = source.get(version)
    if not Path(src.artifact_uri).is_dir():
        raise typer.BadParameter(f"El artefacto de {version} no es una carpeta local ({src.artifact_uri})")
    info = c.registry.publish(version, src.artifact_uri, {
        "base_checkpoint": src.base_checkpoint, "parent": src.parent, "status": "candidate", "imported_from": str(from_workspace),
        "source_sha256": src.artifact_sha256, "seed": src.metadata.get("seed"), "dataset_snapshot": src.metadata.get("dataset_snapshot"),
        "description": "Importado de otro espacio de trabajo sin calibración (todos sus campos van a revisión hasta recalibrar)",
    })
    c.repo.upsert_model_version(info.__dict__)
    same = "igual" if info.artifact_sha256 == src.artifact_sha256 else "DISTINTO"
    typer.secho(f"Importado {version} (sha256 {info.artifact_sha256[:12]}…, {same} al de origen)", fg="green")


@registry_app.command("list")
def registry_list(profile: str | None = ProfileOpt, workspace: str | None = WorkspaceOpt) -> None:
    """Versiones de modelo con su estado, padre, calibración y compuertas."""
    from perfo.ml.learning_cycle import gate_summary  # noqa: PLC0415

    c = container(profile, workspace)
    active = c.registry.get_active()
    for i in c.registry.list():
        cal = c.registry.read_attachment(i.version, "calibration")
        gates = c.registry.read_attachment(i.version, "gates")
        mark = "*" if active and i.version == active.version else " "
        typer.echo(f"{mark} {i.version:<34} {i.status:<10} padre={i.parent or '-':<34} calibración={'sí' if cal else 'no'}  {gate_summary(gates['gates']) if gates else ''}")


@app.command("e2e")
def e2e_cmd(
    profile: str = typer.Option("smoke", "--profile", "-p", help="smoke | full"),
    device: str | None = typer.Option(None, "--device", help="auto | cpu | mps | cuda (por defecto, el de la configuración)"),
    workspace: str | None = WorkspaceOpt,
) -> None:
    """Flujo de prueba de extremo a extremo: genera, procesa, ajusta, calibra, valida, aprende y reporta."""
    from perfo.e2e import run_e2e  # noqa: PLC0415

    R = run_e2e(profile, device=device, workspace=workspace, log=typer.echo)
    typer.secho(f"Reporte: {R['report_paths']['markdown']}", fg="green")


def _latest_e2e_workspace(profile: str) -> str | None:
    """Most recent `perfo e2e` run of the profile (data/e2e/<perfil>/<fecha_hora>), or None to use the profile workspace."""
    cfg = load_config(profile)
    base = cfg.resolve(cfg["workspace"])
    if (base / "perfo.sqlite").exists() or not base.is_dir():
        return None
    runs = sorted(p for p in base.iterdir() if (p / "perfo.sqlite").exists() and (p / "registry" / "registry.json").exists())
    return str(runs[-1]) if runs else None


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", help="Interfaz de red de la API"),
    port: int = typer.Option(8000, help="Puerto de la API"),
    profile: str = typer.Option("smoke", "--profile", "-p", help="smoke | full"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Carpeta de trabajo (por defecto, la última corrida e2e del perfil)"),
    reference: Path | None = ReferenceOpt,
    origins: list[str] = typer.Option(None, "--origin", help="Origen adicional permitido por CORS (además de localhost:3100 y :3000)"),
) -> None:
    """API HTTP para la interfaz web: ingesta, reconocimiento, revisión manual, reentrenamiento y promoción."""
    from perfo.adapters.http.api import DEFAULT_ORIGINS, serve  # noqa: PLC0415

    ws = workspace or _latest_e2e_workspace(profile)
    c = container(profile, ws)
    active = c.registry.get_active()
    typer.echo(f"Espacio de trabajo: {c.workspace}")
    typer.echo(f"Modelo activo: {active.version if active else '(ninguno; se usará la línea base)'} · dispositivo {c.device()}")
    typer.echo(f"API en http://{host}:{port}/api (documentación interactiva en http://{host}:{port}/docs)")
    serve(c, host, port, str(reference) if reference else None, [*DEFAULT_ORIGINS, *(origins or [])])


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


def main() -> None:
    """Entry point: domain errors are shown as clean messages instead of tracebacks."""
    from perfo.application.ingest import IngestionRejected  # noqa: PLC0415
    from perfo.application.validation import ValidationBlocked  # noqa: PLC0415
    from perfo.ml.dataset import LeakageError  # noqa: PLC0415
    from perfo.ml.train import TrainingFailed  # noqa: PLC0415

    try:
        app(standalone_mode=False)
    except ValidationBlocked as exc:
        typer.secho(f"Bloqueado: {exc}", fg="red", err=True)
        for f in exc.fields[:30]:
            typer.echo(f"  - {f}", err=True)
        raise SystemExit(1) from None
    except (IngestionRejected, LeakageError, TrainingFailed, KeyError, ValueError) as exc:
        typer.secho(f"Error: {exc}", fg="red", err=True)
        raise SystemExit(1) from None
    except typer.Exit as exc:
        raise SystemExit(exc.exit_code) from None
    except click_exceptions() as exc:  # usage errors
        exc.show()
        raise SystemExit(exc.exit_code) from None


def click_exceptions() -> tuple[type[BaseException], ...]:
    import click  # noqa: PLC0415

    return (click.ClickException,)


if __name__ == "__main__":
    main()
