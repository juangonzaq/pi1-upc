from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DocumentIngestionPort(ABC):
    @abstractmethod
    def submit(self, document: bytes, metadata: dict[str, Any]) -> str:
        """Registers a document (hash, metadata) and returns its id. Idempotent by content hash."""
