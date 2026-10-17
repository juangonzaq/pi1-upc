"""Ground-truth access. Synthetic: per-form JSON from the generator; real: double-entry reference (same interface)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from perfo.ml.metrics import literal


class GroundTruth:
    """Maps a document (by its form_id metadata) to its field-level truth."""

    def __init__(self, synth_dir: str | Path):
        self.dir = Path(synth_dir)
        man = json.loads((self.dir / "manifest.json").read_text())
        self.manifest = man
        self._forms = {f["form_id"]: f for f in man["forms"]}

    def form(self, form_id: str) -> dict[str, Any]:
        return self._forms[form_id]

    @lru_cache(maxsize=4096)  # noqa: B019
    def fields(self, form_id: str) -> dict[str, dict[str, Any]]:
        if form_id not in self._forms:
            return {}
        return json.loads((self.dir / self._forms[form_id]["gt"]).read_text())["fields"]

    def value(self, form_id: str, field_id: str) -> str | None:
        f = self.fields(form_id).get(field_id)
        return f["value"] if f else None

    def literal(self, form_id: str, field_id: str) -> str | None:
        """Uppercased literal text; None for empty cells, repetition marks and diagonal writing."""
        f = self.fields(form_id).get(field_id)
        if not f or not f["text"]:
            return None
        return literal(f["text"])

    def mark(self, form_id: str, field_id: str) -> str | None:
        f = self.fields(form_id).get(field_id)
        return f["mark"] if f else None


class ReferenceGroundTruth(GroundTruth):
    """Real data: double-entry reference transcription (HU02) with the same interface.

    CSV columns: archivo, campo, valor, texto[, operador]. `archivo` is the scanned file name (source_name), used as form_id;
    `valor` is the canonical value (codes for masters, HH:MM, ISO dates); `texto` is the literal transcription.
    """

    def __init__(self, csv_path: str | Path):  # noqa: D107 (no synthetic manifest)
        import csv  # noqa: PLC0415

        self.dir = Path(csv_path).parent
        self.manifest = {"forms": [], "supervisors": []}
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._forms = {}
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                fid = row["archivo"]
                self._data.setdefault(fid, {})[row["campo"]] = {"value": row.get("valor") or None, "text": row.get("texto") or None, "mark": None}
                self._forms.setdefault(fid, {"form_id": fid, "operator": row.get("operador"), "gt": None})

    def fields(self, form_id: str) -> dict[str, dict[str, Any]]:  # type: ignore[override]
        return self._data.get(form_id, {})


def load_ground_truth(workspace: Path, reference_csv: str | Path | None = None) -> GroundTruth:
    """Reference CSV when given (real data); otherwise the synthetic manifest of the workspace."""
    if reference_csv:
        return ReferenceGroundTruth(reference_csv)
    return GroundTruth(Path(workspace) / "synth")
