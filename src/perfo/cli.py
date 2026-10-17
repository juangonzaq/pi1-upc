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

    tpl = build_template_v1()
    out.write_text(json.dumps(tpl, indent=1, ensure_ascii=False))
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
