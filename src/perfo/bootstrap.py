"""Composition root: wires adapters to ports from configuration."""

from __future__ import annotations

import hashlib
import json
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from perfo.adapters.masters_file.masters import FileMasterData
from perfo.adapters.queue_inproc.queue import InProcessQueue
from perfo.adapters.registry_disk.registry import DiskModelRegistry
from perfo.adapters.sqlite.repository import SqlRepository
from perfo.adapters.storage_fs.storage import FileSystemStorage
from perfo.adapters.telemetry_log.telemetry import JsonlTelemetry
from perfo.config import Config
from perfo.domain.fields import Template, parse_template


class Container:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.workspace = cfg.resolve(cfg["workspace"])
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._recognizers: dict[str, Any] = {}

    # ---------------------------------------------------------------- infrastructure
    @cached_property
    def repo(self) -> SqlRepository:
        url = self.cfg.get_path("database_url") or f"sqlite:///{self.workspace / 'perfo.sqlite'}"
        return SqlRepository(url)

    @cached_property
    def storage(self) -> FileSystemStorage:
        return FileSystemStorage(self.workspace / "objects")

    @cached_property
    def registry(self) -> DiskModelRegistry:
        backend = self.cfg.get_path("registry.backend", "disk")
        root = self.workspace / "registry"
        if backend == "mlflow":
            from perfo.adapters.registry_disk.registry import MlflowModelRegistry  # noqa: PLC0415

            return MlflowModelRegistry(root, self.cfg.get_path("registry.tracking_uri", f"file:{self.workspace / 'mlruns'}"))
        return DiskModelRegistry(root)

    @cached_property
    def telemetry(self) -> JsonlTelemetry:
        return JsonlTelemetry(self.workspace / "telemetry" / "events.jsonl")

    @cached_property
    def queue(self) -> InProcessQueue:
        return InProcessQueue(eager=False)

    @cached_property
    def abbreviations(self) -> dict[str, dict[str, str]]:
        data = yaml.safe_load(self.cfg.resolve(self.cfg["abbreviations"]).read_text()) or {}
        return {k: {str(a).upper(): str(b or "") for a, b in (v or {}).items()} for k, v in data.items()}

    @cached_property
    def masters(self) -> FileMasterData:
        return FileMasterData(self.repo, self.abbreviations)

    # ---------------------------------------------------------------- template
    @cached_property
    def template_definition(self) -> dict[str, Any]:
        return json.loads(self.cfg.resolve(self.cfg["template"]).read_text())

    @cached_property
    def template(self) -> Template:
        definition = self.template_definition
        sha = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        self.repo.save_template(definition["template_id"], str(definition["version"]), definition, sha)
        return parse_template(definition)

    def device(self) -> str:
        want = self.cfg.get_path("device", "auto")
        if want != "auto":
            return want
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def path(self, *parts: str) -> Path:
        p = self.workspace.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
