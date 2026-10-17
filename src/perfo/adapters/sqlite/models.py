"""SQLAlchemy schema. Portable types only, so the same schema runs on SQLite and PostgreSQL 16."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")
TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    original_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(128))
    uploaded_at: Mapped[datetime] = mapped_column(TS)
    business_key: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    state_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preprocessed_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    transform: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class DocumentStateEventRow(Base):
    __tablename__ = "document_state_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32))
    cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    at: Mapped[datetime] = mapped_column(TS)


class PageRow(Base):
    __tablename__ = "pages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    dpi_equivalent: Mapped[float] = mapped_column(Float)


class TemplateRow(Base):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("template_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(TS)


class CropRow(Base):
    __tablename__ = "crops"
    __table_args__ = (UniqueConstraint("document_id", "field_id", "template_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id"))
    field_id: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(32))
    box_px: Mapped[list[int]] = mapped_column(JsonType)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_empty: Mapped[bool] = mapped_column(Boolean)
    ink_ratio: Mapped[float] = mapped_column(Float)
    mark: Mapped[str | None] = mapped_column(String(16), nullable=True)


class RecognitionRow(Base):
    __tablename__ = "recognitions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    crop_id: Mapped[str] = mapped_column(ForeignKey("crops.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliable: Mapped[bool] = mapped_column(Boolean)
    calibration_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(TS)


class FieldValueRow(Base):
    __tablename__ = "field_values"
    __table_args__ = (Index("ix_field_values_doc_run", "document_id", "run_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    run_id: Mapped[str] = mapped_column(String(32))
    seq: Mapped[int] = mapped_column(Integer)
    field_id: Mapped[str] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(16))
    crop_id: Mapped[str | None] = mapped_column(ForeignKey("crops.id"), nullable=True)
    recognition_id: Mapped[str | None] = mapped_column(ForeignKey("recognitions.id"), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    route: Mapped[str] = mapped_column(String(8))
    reasons: Mapped[list[str]] = mapped_column(JsonType)
    violations: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    mark: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)


class HomologationRow(Base):
    __tablename__ = "homologations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    field_value_id: Mapped[str] = mapped_column(ForeignKey("field_values.id"), index=True)
    master_type: Mapped[str] = mapped_column(String(32))
    master_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_text: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(16))
    accepted: Mapped[bool] = mapped_column(Boolean)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)


class ValidatedRecordRow(Base):
    __tablename__ = "validated_records"
    __table_args__ = (UniqueConstraint("document_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    validated_by: Mapped[str] = mapped_column(String(128))
    validated_at: Mapped[datetime] = mapped_column(TS)
    sha256: Mapped[str] = mapped_column(String(64))
    supersedes: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JsonType)


class ValidatedValueRow(Base):
    __tablename__ = "validated_values"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("validated_records.id"), index=True)
    field_id: Mapped[str] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    field_value_id: Mapped[str | None] = mapped_column(ForeignKey("field_values.id"), nullable=True)


class CorrectionRow(Base):
    __tablename__ = "corrections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    field_value_id: Mapped[str] = mapped_column(ForeignKey("field_values.id"))
    crop_id: Mapped[str | None] = mapped_column(ForeignKey("crops.id"), nullable=True)
    field_id: Mapped[str] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(16))
    recognized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    recognized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TS)
    kind: Mapped[str] = mapped_column(String(32))
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("validated_records.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(Text)
    approver: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TS)


class MasterVersionRow(Base):
    __tablename__ = "master_versions"
    __table_args__ = (UniqueConstraint("master_type", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_type: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(TS)
    source_name: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    errors: Mapped[list[str]] = mapped_column(JsonType)


class MasterItemRow(Base):
    __tablename__ = "master_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_version_id: Mapped[int] = mapped_column(ForeignKey("master_versions.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JsonType)
    representations: Mapped[list[str]] = mapped_column(JsonType)


class AliasRow(Base):
    __tablename__ = "aliases"
    __table_args__ = (UniqueConstraint("master_type", "alias"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_type: Mapped[str] = mapped_column(String(32))
    alias: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(16))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class ModelVersionRow(Base):
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_checkpoint: Mapped[str] = mapped_column(Text)
    parent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    artifact_uri: Mapped[str] = mapped_column(Text)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))
    info: Mapped[dict[str, Any]] = mapped_column(JsonType)


class CalibrationProfileRow(Base):
    __tablename__ = "calibration_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.version"), index=True)
    aggregation: Mapped[str] = mapped_column(String(16))
    profile: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[str] = mapped_column(String(40))


class SplitRow(Base):
    __tablename__ = "dataset_splits"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    frozen: Mapped[bool] = mapped_column(Boolean)
    manifest: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(TS)


class SnapshotRow(Base):
    __tablename__ = "dataset_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    manifest_key: Mapped[str] = mapped_column(Text)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    counts: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(TS)


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(TS, index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
