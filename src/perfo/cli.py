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
