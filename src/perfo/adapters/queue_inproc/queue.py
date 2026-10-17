"""Synchronous in-process queue behind QueuePort (replaceable by Celery + Redis)."""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from perfo.ports.queue import QueuePort


class InProcessQueue(QueuePort):
    def __init__(self, eager: bool = False):
        self.eager = eager
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._pending: deque[tuple[str, str, dict[str, Any]]] = deque()

    def register(self, job_type: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        self._handlers[job_type] = handler

    def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        if job_type not in self._handlers:
            raise KeyError(f"No hay manejador registrado para {job_type}")
        job_id = uuid.uuid4().hex
        self._pending.append((job_id, job_type, payload))
        if self.eager:
            self.run_pending()
        return job_id

    def run_pending(self) -> int:
        n = 0
        while self._pending:
            _, job_type, payload = self._pending.popleft()
            self._handlers[job_type](payload)
            n += 1
        return n
