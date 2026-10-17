"""Model registry on disk: versions, artifacts, evaluation metadata and the active pointer."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from perfo.ports.registry import ModelRegistryPort, ModelVersionInfo


def dir_sha256(path: str | Path) -> str:
    """Deterministic hash of a directory (relative paths + contents)."""
    root = Path(path)
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(str(p.relative_to(root)).encode())
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


class DiskModelRegistry(ModelRegistryPort):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "models").mkdir(parents=True, exist_ok=True)
        self._index = self.root / "registry.json"
        if not self._index.exists():
            self._save_index({"active": None, "history": []})

    # ---------------------------------------------------------------- internals
    def _load_index(self) -> dict[str, Any]:
        return json.loads(self._index.read_text())

    def _save_index(self, data: dict[str, Any]) -> None:
        tmp = self._index.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self._index)

    def _vdir(self, version: str) -> Path:
        return self.root / "models" / version

    def _read_meta(self, version: str) -> dict[str, Any]:
        p = self._vdir(version) / "metadata.json"
        if not p.exists():
            raise KeyError(f"Versión de modelo no registrada: {version}")
        return json.loads(p.read_text())

    def _write_meta(self, version: str, meta: dict[str, Any]) -> None:
        (self._vdir(version) / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str))

    @staticmethod
    def _info(meta: dict[str, Any]) -> ModelVersionInfo:
        return ModelVersionInfo(
            version=meta["version"],
            base_checkpoint=meta["base_checkpoint"],
            parent=meta.get("parent"),
            status=meta["status"],
            artifact_uri=meta["artifact_uri"],
            artifact_sha256=meta.get("artifact_sha256"),
            created_at=meta["created_at"],
            metadata=meta.get("metadata", {}),
        )

    # ---------------------------------------------------------------- port
    def publish(self, version: str, artifact_dir: str | None, metadata: dict[str, Any]) -> ModelVersionInfo:
        vdir = self._vdir(version)
        if (vdir / "metadata.json").exists():
            raise ValueError(f"La versión {version} ya existe en el registro")
        vdir.mkdir(parents=True)
        if artifact_dir:
            target = vdir / "artifact"
            shutil.copytree(artifact_dir, target)
            uri, sha = str(target), dir_sha256(target)
        else:
            uri = metadata["artifact_uri"]
            sha = metadata.get("artifact_sha256")
        meta = {
            "version": version,
            "base_checkpoint": metadata["base_checkpoint"],
            "parent": metadata.get("parent"),
            "status": metadata.get("status", "candidate"),
            "artifact_uri": uri,
            "artifact_sha256": sha,
            "created_at": datetime.now(UTC).isoformat(),
            "metadata": {k: v for k, v in metadata.items() if k not in {"base_checkpoint", "parent", "status", "artifact_uri", "artifact_sha256"}},
        }
        self._write_meta(version, meta)
        return self._info(meta)

    def get(self, version: str) -> ModelVersionInfo:
        return self._info(self._read_meta(version))

    def get_active(self) -> ModelVersionInfo | None:
        active = self._load_index().get("active")
        return self.get(active) if active else None

    def set_active(self, version: str, approval: dict[str, Any]) -> None:
        idx = self._load_index()
        previous = idx.get("active")
        if previous and previous != version:
            self.set_status(previous, "retired", f"reemplazada por {version}")
        self.set_status(version, "active", "promovida")
        idx["active"] = version
        idx["history"].append({"version": version, "previous": previous, "at": datetime.now(UTC).isoformat(), **approval})
        self._save_index(idx)

    def set_status(self, version: str, status: str, note: str = "") -> None:
        meta = self._read_meta(version)
        meta["status"] = status
        meta.setdefault("status_log", []).append({"status": status, "note": note, "at": datetime.now(UTC).isoformat()})
        self._write_meta(version, meta)

    def list(self) -> list[ModelVersionInfo]:
        out = []
        for p in sorted((self.root / "models").glob("*/metadata.json")):
            out.append(self._info(json.loads(p.read_text())))
        return sorted(out, key=lambda i: i.created_at)

    def attach(self, version: str, name: str, data: dict[str, Any]) -> str:
        self._read_meta(version)
        adir = self._vdir(version) / "attachments"
        adir.mkdir(exist_ok=True)
        path = adir / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return str(path)

    def read_attachment(self, version: str, name: str) -> dict[str, Any] | None:
        path = self._vdir(version) / "attachments" / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def history(self) -> list[dict[str, Any]]:
        return self._load_index().get("history", [])


class MlflowModelRegistry(DiskModelRegistry):
    """Optional adapter: keeps the disk registry as source of truth and mirrors to MLflow.

    Requires the `mlflow` extra (pip install -e '.[mlflow]').
    """

    def __init__(self, root: str | Path, tracking_uri: str, experiment: str = "perforacion-ia"):
        super().__init__(root)
        import mlflow  # noqa: PLC0415  (optional dependency)

        self._mlflow = mlflow
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment)

    def publish(self, version: str, artifact_dir: str | None, metadata: dict[str, Any]) -> ModelVersionInfo:
        info = super().publish(version, artifact_dir, metadata)
        with self._mlflow.start_run(run_name=version):
            self._mlflow.log_params({"version": version, "base_checkpoint": info.base_checkpoint, "parent": info.parent or ""})
            self._mlflow.set_tag("artifact_sha256", info.artifact_sha256 or "")
        return info

    def attach(self, version: str, name: str, data: dict[str, Any]) -> str:
        path = super().attach(version, name, data)
        with self._mlflow.start_run(run_name=f"{version}:{name}"):
            numeric = {k: float(v) for k, v in data.items() if isinstance(v, int | float)}
            if numeric:
                self._mlflow.log_metrics(numeric)
            self._mlflow.log_artifact(path)
        return path
