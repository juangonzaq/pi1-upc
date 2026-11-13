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
        # In-process queue; the durable queue is the EN_COLA state in the repository,
        # so `perfo process` can resume pending work in a later run.
        q = InProcessQueue(eager=False)
        q.register("process_document", lambda payload: self.processor().process(payload["document_id"]))
        return q

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

    @cached_property
    def alternative_templates(self) -> list[tuple[Template, str]]:
        """Other printed forms of the profile, tried when the main template does not recognize the image."""
        found = []
        for path in self.cfg.get_path("extra_templates", []) or []:
            definition = json.loads(self.cfg.resolve(path).read_text())
            reference = self.cfg.resolve(definition["reference_image"])
            if not reference.exists():  # built from real photos, out of git: `make fisico-templates`
                continue
            sha = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
            self.repo.save_template(definition["template_id"], str(definition["version"]), definition, sha)
            found.append((parse_template(definition), str(reference)))
        return found

    def template_for(self, version: str | None) -> Template:
        """The template a document was read with."""
        return next((t for t, _ in self.alternative_templates if t.version == version), self.template)

    @cached_property
    def spec_of(self) -> dict[str, Any]:
        """Every field id of every template; an id shared with the main template keeps its definition."""
        specs = {f.id: f for t, _ in reversed(self.alternative_templates) for f in t.fields}
        return {**specs, **{f.id: f for f in self.template.fields}}

    # ---------------------------------------------------------------- application services
    @cached_property
    def ingestion(self):  # noqa: ANN201
        from perfo.application.ingest import IngestionService  # noqa: PLC0415

        return IngestionService(self.repo, self.storage, self.queue, self.telemetry)

    @cached_property
    def homologator(self):  # noqa: ANN201
        from perfo.application.homologation import Homologator  # noqa: PLC0415
        from perfo.domain.homologation import HomologationPolicy  # noqa: PLC0415

        h = self.cfg["thresholds"]["homologation"]
        policies = {}
        for mtype in ("labores", "equipos", "personal"):
            per = (h.get("per_master") or {}).get(mtype, {})
            policies[mtype] = HomologationPolicy(
                threshold=float(per.get("threshold", h["threshold"])),
                min_margin=float(per.get("min_margin", h["min_margin"])),
                lev_weight=float(per.get("lev_weight", h["lev_weight"])),
            )
        return Homologator(self.masters, self.abbreviations, policies)

    @cached_property
    def validation(self):  # noqa: ANN201
        from perfo.application.validation import ValidationService  # noqa: PLC0415

        return ValidationService(self.repo, self.masters, self.homologator, self.template, self.telemetry, self.template_for)

    def ensure_baseline(self):  # noqa: ANN201
        """Registers the pretrained checkpoint as baseline and activates it when the registry is empty."""
        ck = self.cfg["model"]["base_checkpoint"]
        version = "baseline-" + ck.split("/")[-1]
        try:
            info = self.registry.get(version)
        except KeyError:
            info = self.registry.publish(version, None, {"base_checkpoint": ck, "artifact_uri": ck, "status": "baseline",
                                                         "description": "Modelo preentrenado sin ajuste (línea base)"})
            self.repo.upsert_model_version(info.__dict__)
        if self.registry.get_active() is None:
            self.registry.set_active(version, {"approver": "sistema", "reason": "línea base inicial (sin calibración)"})
            self.repo.upsert_model_version(self.registry.get(version).__dict__)
        return info

    def calibration_profile(self, version: str | None = None) -> dict | None:
        info = self.registry.get(version) if version else self.registry.get_active()
        return self.registry.read_attachment(info.version, "calibration") if info else None

    def routing_policy(self, version: str | None = None):  # noqa: ANN201
        from perfo.domain.routing import RoutingPolicy  # noqa: PLC0415

        prof = self.calibration_profile(version) or {}
        return RoutingPolicy(thresholds=dict(prof.get("thresholds", {})),
                             repeat_mark_forces_review=bool(self.cfg.get_path("routing.repeat_mark_forces_review", True)),
                             low_resolution_forces_review=bool(self.cfg.get_path("routing.low_resolution_forces_review", True)))

    def has_weights(self, version: str) -> bool:
        """A registered version can be read with only while its weights exist (the base model comes from its checkpoint id)."""
        artifact = Path(self.registry.get(version).artifact_uri or "")
        return not artifact.is_dir() or any(artifact.glob("*.safetensors"))

    def recognizer(self, version: str | None = None, calibrated: bool = True):  # noqa: ANN201
        from perfo.adapters.trocr.recognizer import TrocrRecognizer  # noqa: PLC0415

        info = self.registry.get(version) if version else (self.registry.get_active() or self.ensure_baseline())
        key = f"{info.version}|{calibrated}"
        if key not in self._recognizers:
            m = self.cfg["model"]
            self._recognizers[key] = TrocrRecognizer(
                info.artifact_uri, info.version, self.device(), m.get("aggregation", "mean_logprob"),
                self.calibration_profile(info.version) if calibrated else None, int(m.get("batch_size", 32)), int(m.get("max_new_tokens", 24)),
            )
        return self._recognizers[key]

    def processor(self, actor: str = "sistema", version: str | None = None, recognizer=None):  # noqa: ANN001, ANN201
        from perfo.application.processing import DocumentProcessor  # noqa: PLC0415

        rec = recognizer
        return DocumentProcessor(
            self.repo, self.storage, self.telemetry, self.template, self.reference_path, self.cfg,
            (lambda: rec) if rec is not None else (lambda: self.recognizer(version)),
            self.homologator, self.masters, lambda: self.routing_policy(version), actor, self.alternative_templates,
        )

    @cached_property
    def reference_path(self) -> str:
        return str(self.cfg.resolve(self.template_definition["reference_image"]))

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
