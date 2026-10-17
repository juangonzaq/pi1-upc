from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any


class TelemetryPort(ABC):
    @abstractmethod
    def metric(self, name: str, value: float, **tags: Any) -> None: ...

    @abstractmethod
    def event(self, name: str, **fields: Any) -> None: ...

    @abstractmethod
    def timer(self, name: str, **tags: Any) -> AbstractContextManager[dict[str, float]]:
        """Context manager that records the elapsed seconds as a metric."""

    @abstractmethod
    def values(self, name: str, **tags: Any) -> list[float]:
        """Recorded values of a metric (in-process view, for reports)."""
