from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any

from perfo.domain.entities import (
    Alias,
    Approval,
    AuditEvent,
    Correction,
    Crop,
    Document,
    FieldValue,
    MasterVersion,
    Page,
    Recognition,
    ValidatedRecord,
)


class RepositoryPort(ABC):
    """Transactional persistence of aggregates, events and metrics."""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """Groups several writes atomically."""

    # documents ---------------------------------------------------------
    @abstractmethod
    def add_document(self, doc: Document) -> None: ...

    @abstractmethod
    def update_document(self, doc: Document) -> None: ...

    @abstractmethod
    def get_document(self, doc_id: str) -> Document | None: ...

    @abstractmethod
    def find_document_by_hash(self, sha256: str) -> Document | None: ...

    @abstractmethod
    def find_documents_by_business_key(self, key: str) -> list[Document]: ...

    @abstractmethod
    def list_documents(self, states: list[str] | None = None, **filters: Any) -> list[Document]: ...

    @abstractmethod
    def record_state_change(self, doc_id: str, from_state: str | None, to_state: str, cause: str | None, actor: str) -> None: ...

    @abstractmethod
    def state_history(self, doc_id: str) -> list[dict[str, Any]]: ...

    # pages, templates, crops -------------------------------------------
    @abstractmethod
    def add_page(self, page: Page) -> None: ...

    @abstractmethod
    def get_pages(self, doc_id: str) -> list[Page]: ...

    @abstractmethod
    def save_template(self, template_id: str, version: str, definition: dict[str, Any], sha256: str) -> None: ...

    @abstractmethod
    def get_template_definition(self, template_id: str, version: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def upsert_crops(self, crops: list[Crop]) -> list[Crop]:
        """Idempotent by (document, field, template version). Returns the stored crops."""

    @abstractmethod
    def get_crops(self, doc_id: str) -> list[Crop]: ...

    @abstractmethod
    def get_crop(self, crop_id: str) -> Crop | None: ...

    # recognition and field values ---------------------------------------
    @abstractmethod
    def add_recognitions(self, recs: list[Recognition]) -> None: ...

    @abstractmethod
    def get_recognitions(self, crop_ids: list[str], model_version: str | None = None) -> list[Recognition]: ...

    @abstractmethod
    def add_field_values(self, values: list[FieldValue]) -> None: ...

    @abstractmethod
    def get_field_values(self, doc_id: str, run_id: str | None = None) -> list[FieldValue]:
        """Values of the given run, or of the latest run when run_id is None."""

    @abstractmethod
    def get_field_value(self, fv_id: str) -> FieldValue | None: ...

    @abstractmethod
    def field_value_counts(self) -> dict[str, dict[str, int]]:
        """Per document, number of fields by route ({"AUTO": n, "REVIEW": n}) in its latest run."""

    # validation, corrections, approvals ---------------------------------
    @abstractmethod
    def add_validated_record(self, record: ValidatedRecord) -> None: ...

    @abstractmethod
    def get_records(self, doc_id: str) -> list[ValidatedRecord]: ...

    @abstractmethod
    def add_corrections(self, corrections: list[Correction]) -> None: ...

    @abstractmethod
    def list_corrections(self, unused_only: bool = True, kinds: list[str] | None = None) -> list[Correction]: ...

    @abstractmethod
    def mark_corrections_used(self, ids: list[str], snapshot_id: str) -> None: ...

    @abstractmethod
    def add_approval(self, approval: Approval) -> None: ...

    @abstractmethod
    def get_approvals(self, doc_id: str) -> list[Approval]: ...

    # masters and aliases ------------------------------------------------
    @abstractmethod
    def save_master_version(self, mv: MasterVersion) -> None: ...

    @abstractmethod
    def get_master_version(self, master_type: str, version: int | None = None) -> MasterVersion | None:
        """The given version, or the active one when version is None."""

    @abstractmethod
    def find_master_by_hash(self, master_type: str, sha256: str) -> MasterVersion | None: ...

    @abstractmethod
    def next_master_version(self, master_type: str) -> int: ...

    @abstractmethod
    def set_master_status(self, master_type: str, version: int, status: str) -> None: ...

    @abstractmethod
    def upsert_alias(self, alias: Alias) -> Alias: ...

    @abstractmethod
    def get_aliases(self, master_type: str) -> list[Alias]: ...

    @abstractmethod
    def mark_aliases_for_review(self, master_type: str, valid_codes: set[str]) -> int: ...

    # models, calibration, datasets --------------------------------------
    @abstractmethod
    def upsert_model_version(self, info: dict[str, Any]) -> None: ...

    @abstractmethod
    def add_calibration_profile(self, profile: dict[str, Any]) -> None: ...

    @abstractmethod
    def save_split(self, version: str, manifest: dict[str, Any], frozen: bool) -> None: ...

    @abstractmethod
    def get_split(self, version: str | None = None) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_snapshot(self, snapshot: dict[str, Any]) -> None: ...

    @abstractmethod
    def list_snapshots(self) -> list[dict[str, Any]]: ...

    # audit ---------------------------------------------------------------
    @abstractmethod
    def add_audit(self, event: AuditEvent) -> None: ...
