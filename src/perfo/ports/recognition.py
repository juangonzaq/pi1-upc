from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class RecognizerUnavailable(Exception):
    """The recognizer cannot serve requests (model not loaded, device failure, ...)."""


@dataclass(frozen=True)
class RecognitionOutput:
    text: str
    score: float | None  # normalized confidence in [0, 1]; calibrated when a profile exists
    model_version: str
    raw_score: float | None = None
    reliable: bool = False
    calibration_id: str | None = None
    latency_ms: float = 0.0


class RecognitionPort(ABC):
    @property
    @abstractmethod
    def model_version(self) -> str: ...

    @abstractmethod
    def recognize(self, field_image: bytes, field_type: str) -> RecognitionOutput:
        """field_image: PNG bytes of one field crop."""

    def recognize_batch(self, items: list[tuple[bytes, str]]) -> list[RecognitionOutput]:
        return [self.recognize(img, ftype) for img, ftype in items]
