"""Domain entities. Recognition result, final value and correction are kept separate so nothing is overwritten."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from perfo.domain.states import DocumentState


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Document:
    id: str
    sha256: str
    original_key: str
    source_name: str
    uploaded_by: str
    uploaded_at: datetime
    state: DocumentState
    content_type: str = "image/jpeg"
    business_key: str | None = None  # fecha|guardia|equipo when known at intake
    state_cause: str | None = None
    duplicate_of: str | None = None
    template_version: str | None = None
    preprocessed_key: str | None = None
    transform: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Page:
    id: str
    document_id: str
    page_no: int
    object_key: str
    width: int
    height: int
    dpi_equivalent: float


@dataclass
class Crop:
    id: str
    document_id: str
    page_id: str
    field_id: str
    template_version: str
    box_px: tuple[int, int, int, int]
    object_key: str | None
    sha256: str | None
    is_empty: bool
    ink_ratio: float
    mark: str | None = None  # "repeat" when a repetition stroke is detected


@dataclass
class Recognition:
    id: str
    crop_id: str
    model_version: str
    text: str
    score: float | None  # calibrated when a calibration profile exists
    raw_score: float | None  # uncalibrated aggregation (T = 1)
    reliable: bool
    calibration_id: str | None
    latency_ms: float
    created_at: datetime = field(default_factory=now)


@dataclass
class HomologationResult:
    master_type: str
    master_version: int | None
    input_text: str
    normalized: str
    code: str | None
    score: float
    method: str  # exact | alias | fuzzy | none
    accepted: bool
    candidates: list[dict[str, Any]] = field(default_factory=list)  # top-3 {code, description, score}


@dataclass
class FieldValue:
    """Proposed (structured) value of a field for one processing run."""

    id: str
    document_id: str
    run_id: str
    field_id: str
    field_type: str
    crop_id: str | None
    recognition_id: str | None
    text: str | None
    value: str | None
    confidence: float | None
    route: str
    reasons: list[str]
    violations: list[dict[str, Any]]
    homologation: HomologationResult | None = None
    mark: str | None = None


@dataclass
class ValidatedValue:
    field_id: str
    value: str | None
    source: str  # auto | confirmed | corrected | candidate
    field_value_id: str | None


@dataclass
class ValidatedRecord:
    id: str
    document_id: str
    version: int
    validated_by: str
    validated_at: datetime
    values: list[ValidatedValue]
    sha256: str
    supersedes: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class Correction:
    id: str
    document_id: str
    field_value_id: str
    crop_id: str | None
    field_id: str
    field_type: str
    recognized_text: str | None
    recognized_value: str | None
    validated_value: str | None
    validated_text: str | None  # literal transcription when the validator provides it
    confidence: float | None
    model_version: str | None
    user: str
    created_at: datetime
    kind: str = "correction"  # correction | confirmed_positive
    snapshot_id: str | None = None


@dataclass
class Approval:
    id: str
    record_id: str
    document_id: str
    decision: str  # APROBADO | OBSERVADO
    comment: str
    approver: str
    created_at: datetime


@dataclass
class MasterItem:
    code: str
    description: str
    extra: dict[str, Any] = field(default_factory=dict)
    representations: list[str] = field(default_factory=list)


@dataclass
class MasterVersion:
    master_type: str
    version: int
    imported_at: datetime
    source_name: str
    sha256: str
    status: str  # active | superseded | rejected
    items: list[MasterItem]
    errors: list[str] = field(default_factory=list)


@dataclass
class Alias:
    master_type: str
    alias: str  # normalized text
    code: str
    created_by: str
    created_at: datetime
    source: str = "validator"
    needs_review: bool = False


@dataclass
class AuditEvent:
    action: str
    entity: str
    entity_id: str
    actor: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    correlation_id: str | None = None
    at: datetime = field(default_factory=now)
