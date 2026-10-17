"""MasterDataPort: imports versioned masters from CSV/Excel exports of the area."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path

from perfo.domain.entities import Alias, AuditEvent, MasterItem, MasterVersion
from perfo.domain.normalization import basic_normalize, normalize_text
from perfo.ports.masters import MasterDataPort
from perfo.ports.repository import RepositoryPort

REQUIRED_COLUMNS = {
    "labores": ["codigo", "descripcion"],
    "equipos": ["codigo", "descripcion"],
    "personal": ["codigo", "nombre"],
    "codigos": ["tipo", "codigo", "descripcion"],
}
CODE_KINDS = {"actividad", "demora", "falla"}


class MasterNotFound(Exception):
    pass


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook  # noqa: PLC0415

        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = [[("" if c is None else str(c)).strip() for c in r] for r in ws.iter_rows(values_only=True)]
        header, body = rows[0], rows[1:]
    else:
        text = path.read_bytes().decode("utf-8-sig")
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
        reader = list(csv.reader(io.StringIO(text), dialect))
        header, body = reader[0], reader[1:]
    cols = [basic_normalize(h).lower().replace(" ", "_") for h in header]
    out = []
    for r in body:
        if not any(x.strip() for x in r):
            continue
        out.append({cols[i]: (r[i].strip() if i < len(r) else "") for i in range(len(cols))})
    return cols, out


def person_representations(name: str) -> list[str]:
    """Name variants usually written on the form: 'J. QUISPE', 'QUISPE J', 'JUAN QUISPE'."""
    if "," in name:
        surnames, given = [p.strip() for p in name.split(",", 1)]
    else:
        parts = name.split()
        given, surnames = " ".join(parts[:1]), " ".join(parts[1:])
    s = basic_normalize(surnames).split()
    g = basic_normalize(given).split()
    if not s or not g:
        return [basic_normalize(name)]
    reps = {
        f"{g[0]} {s[0]}",
        f"{s[0]} {g[0]}",
        f"{g[0][0]} {s[0]}",
        f"{s[0]} {g[0][0]}",
        " ".join(g + s),
        " ".join(s + g),
        " ".join(s),
    }
    return sorted(reps)


class FileMasterData(MasterDataPort):
    def __init__(self, repo: RepositoryPort, abbreviations: dict[str, dict[str, str]]):
        self.repo = repo
        self.abbreviations = abbreviations

    def _norm(self, master_type: str, text: str) -> str:
        return normalize_text(text, self.abbreviations.get(master_type, {}))

    def _representations(self, master_type: str, row: dict[str, str]) -> list[str]:
        reps: set[str] = set()
        if master_type == "personal":
            for r in person_representations(row["nombre"]):
                reps.add(self._norm(master_type, r))
        else:
            reps.add(self._norm(master_type, row["descripcion"]))
        reps.add(self._norm(master_type, row["codigo"]))
        for alias in (row.get("alias") or "").split("|"):
            if alias.strip():
                reps.add(self._norm(master_type, alias))
        return sorted(r for r in reps if r)

    def import_master(self, master_type: str, file: str) -> tuple[int | None, list[str]]:
        path = Path(file)
        if master_type not in REQUIRED_COLUMNS:
            return None, [f"Tipo de maestro desconocido: {master_type}"]
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        active = self.repo.get_master_version(master_type)
        if active is not None and active.sha256 == sha:
            return active.version, []

        errors: list[str] = []
        try:
            cols, rows = _read_rows(path)
        except Exception as exc:  # noqa: BLE001
            cols, rows = [], []
            errors.append(f"No se pudo leer el archivo: {exc}")
        missing = [c for c in REQUIRED_COLUMNS[master_type] if c not in cols]
        if missing:
            errors.append(f"Columnas faltantes: {', '.join(missing)}")
        items: list[MasterItem] = []
        if not missing and rows:
            seen: dict[str, int] = {}
            for i, row in enumerate(rows, start=2):
                code = row["codigo"].strip()
                desc = (row.get("descripcion") or row.get("nombre") or "").strip()
                key = f"{row.get('tipo', '')}:{code}"
                if not code or not desc:
                    errors.append(f"Fila {i}: código o descripción vacíos")
                    continue
                if master_type == "codigos" and row["tipo"].strip().lower() not in CODE_KINDS:
                    errors.append(f"Fila {i}: tipo de código inválido '{row['tipo']}'")
                if key in seen:
                    errors.append(f"Fila {i}: código duplicado {code} (fila {seen[key]})")
                    continue
                seen[key] = i
                extra = {k: v for k, v in row.items() if k not in ("codigo", "descripcion", "nombre") and v}
                if master_type == "codigos":
                    extra["tipo"] = row["tipo"].strip().lower()
                items.append(MasterItem(code=code, description=desc, extra=extra, representations=self._representations(master_type, row)))
        elif not rows and not errors:
            errors.append("El archivo no contiene filas")

        version = self.repo.next_master_version(master_type)
        now = datetime.now(UTC)
        if errors:
            with self.repo.transaction():
                self.repo.save_master_version(
                    MasterVersion(master_type, version, now, path.name, sha, "rejected", [], errors)
                )
                self.repo.add_audit(AuditEvent("master_rejected", "master", f"{master_type}:{version}", "sistema", after={"errors": errors[:20]}))
            return None, errors

        with self.repo.transaction():
            if active is not None:
                self.repo.set_master_status(master_type, active.version, "superseded")
            self.repo.save_master_version(MasterVersion(master_type, version, now, path.name, sha, "active", items, []))
            flagged = self.repo.mark_aliases_for_review(master_type, {i.code for i in items})
            self.repo.add_audit(
                AuditEvent("master_imported", "master", f"{master_type}:{version}", "sistema",
                           before={"version": active.version if active else None},
                           after={"version": version, "items": len(items), "aliases_flagged": flagged})
            )
        return version, []

    def get_master(self, master_type: str, version: int | None = None) -> MasterVersion:
        mv = self.repo.get_master_version(master_type, version)
        if mv is None:
            raise MasterNotFound(f"No hay maestro '{master_type}' vigente" + (f" v{version}" if version else ""))
        return mv

    def add_alias(self, master_type: str, alias_text: str, code: str, user: str) -> Alias:
        alias = Alias(master_type=master_type, alias=self._norm(master_type, alias_text), code=code, created_by=user, created_at=datetime.now(UTC))
        if not alias.alias:
            return alias
        self.repo.upsert_alias(alias)
        self.repo.add_audit(AuditEvent("alias_learned", "alias", f"{master_type}:{alias.alias}", user, after={"code": code}))
        return alias

    def get_aliases(self, master_type: str) -> list[Alias]:
        return self.repo.get_aliases(master_type)
