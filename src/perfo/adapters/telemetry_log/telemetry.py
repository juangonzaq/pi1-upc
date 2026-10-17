"""Telemetry to a JSONL file plus an in-process view used by reports."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from perfo.ports.telemetry import TelemetryPort


class JsonlTelemetry(TelemetryPort):
    def __init__(self, path: str | Path, echo: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo
        self._values: dict[tuple[str, tuple[tuple[str, Any], ...]], list[float]] = defaultdict(list)

    def _write(self, record: dict[str, Any]) -> None:
        record["ts"] = datetime.now(UTC).isoformat()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def metric(self, name: str, value: float, **tags: Any) -> None:
        self._values[(name, tuple(sorted(tags.items())))].append(float(value))
        self._write({"kind": "metric", "name": name, "value": value, "tags": tags})

    def event(self, name: str, **fields: Any) -> None:
        self._write({"kind": "event", "name": name, **fields})
        if self.echo:
            print(f"[{name}] {fields}")

    @contextmanager
    def timer(self, name: str, **tags: Any) -> Iterator[dict[str, float]]:
        box: dict[str, float] = {}
        t0 = time.perf_counter()
        try:
            yield box
        finally:
            box["seconds"] = time.perf_counter() - t0
            self.metric(name, box["seconds"], **tags)

    def values(self, name: str, **tags: Any) -> list[float]:
        want = set(tags.items())
        out: list[float] = []
        for (n, t), vals in self._values.items():
            if n == name and want.issubset(set(t)):
                out.extend(vals)
        return out
