from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class QueuePort(ABC):
    @abstractmethod
    def register(self, job_type: str, handler: Callable[[dict[str, Any]], Any]) -> None: ...

    @abstractmethod
    def enqueue(self, job_type: str, payload: dict[str, Any]) -> str: ...

    @abstractmethod
    def run_pending(self) -> int:
        """Runs queued jobs (in-process adapter). Returns the number processed."""
