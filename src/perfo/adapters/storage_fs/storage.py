"""Immutable object storage on the local filesystem (same contract as MinIO/S3)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from perfo.ports.storage import ImmutableObjectError, ObjectStoragePort


class FileSystemStorage(ObjectStoragePort):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"Clave de objeto inválida: {key}")
        return p

    def put(self, key: str, data: bytes, metadata: dict[str, Any] | None = None) -> str:
        path = self._path(key)
        digest = hashlib.sha256(data).hexdigest()
        meta_path = path.with_name(path.name + ".meta.json")
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ImmutableObjectError(f"El objeto {key} ya existe con otro contenido")
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        meta = {"sha256": digest, "size": len(data), **(metadata or {})}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True, default=str))
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def get_metadata(self, key: str) -> dict[str, Any]:
        meta = self._path(key).with_name(self._path(key).name + ".meta.json")
        return json.loads(meta.read_text()) if meta.exists() else {}

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_path(self, key: str) -> str | None:
        return str(self._path(key))
