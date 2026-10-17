from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ImmutableObjectError(Exception):
    """Attempt to overwrite an existing object with different content."""


class ObjectStoragePort(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, metadata: dict[str, Any] | None = None) -> str:
        """Stores an immutable object. Same key + same content is idempotent."""

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def get_metadata(self, key: str) -> dict[str, Any]: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def local_path(self, key: str) -> str | None:
        """Local path when the backend is a filesystem (for previews); None otherwise."""
