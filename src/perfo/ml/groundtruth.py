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
