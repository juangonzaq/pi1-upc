from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelVersionInfo:
    version: str
    base_checkpoint: str
    parent: str | None
    status: str  # baseline | candidate | active | rejected | retired
    artifact_uri: str  # local dir or hub id
    artifact_sha256: str | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelRegistryPort(ABC):
    @abstractmethod
    def publish(self, version: str, artifact_dir: str | None, metadata: dict[str, Any]) -> ModelVersionInfo:
        """Registers a version (candidate, or baseline when artifact_dir is None and metadata names a hub checkpoint)."""

    @abstractmethod
    def get(self, version: str) -> ModelVersionInfo: ...

    @abstractmethod
    def get_active(self) -> ModelVersionInfo | None: ...

    @abstractmethod
    def set_active(self, version: str, approval: dict[str, Any]) -> None: ...

    @abstractmethod
    def set_status(self, version: str, status: str, note: str = "") -> None: ...

    @abstractmethod
    def list(self) -> list[ModelVersionInfo]: ...

    @abstractmethod
    def attach(self, version: str, name: str, data: dict[str, Any]) -> str:
        """Stores evaluation metadata (evaluation, calibration, gates, ...) and returns its URI."""

    @abstractmethod
    def read_attachment(self, version: str, name: str) -> dict[str, Any] | None: ...
