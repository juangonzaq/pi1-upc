"""Document intake (HU03): hash, immutable original, duplicate detection and idempotency."""

from __future__ import annotations

import hashlib
from typing import Any

from perfo.domain.entities import AuditEvent, Document, new_id, now
from perfo.domain.states import DocumentState, transition
from perfo.ports.ingestion import DocumentIngestionPort
from perfo.ports.queue import QueuePort
from perfo.ports.repository import RepositoryPort
from perfo.ports.storage import ObjectStoragePort
from perfo.ports.telemetry import TelemetryPort

MAX_BYTES = 10 * 1024 * 1024
MAGIC = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG": ("image/png", "png"), b"%PDF": ("application/pdf", "pdf")}


class IngestionRejected(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def sniff(data: bytes) -> tuple[str, str] | None:
    for magic, info in MAGIC.items():
        if data.startswith(magic):
            return info
    return None


class IngestionService(DocumentIngestionPort):
    def __init__(self, repo: RepositoryPort, storage: ObjectStoragePort, queue: QueuePort, telemetry: TelemetryPort):
        self.repo = repo
        self.storage = storage
        self.queue = queue
        self.telemetry = telemetry
        self.last_outcome: str = ""

    def submit(self, document: bytes, metadata: dict[str, Any]) -> str:
        """Registers a document. A repeated upload (same SHA-256) returns the existing id without a new record."""
        sha = hashlib.sha256(document).hexdigest()
        existing = self.repo.find_document_by_hash(sha)
        if existing is not None:
            self.last_outcome = "repetida"
            self.telemetry.event("ingesta_repetida", document_id=existing.id, source=metadata.get("source_name"))
            self.repo.add_audit(AuditEvent("upload_repeated", "document", existing.id, metadata.get("uploaded_by", "desconocido")))
            return existing.id
        if len(document) > MAX_BYTES:
            raise IngestionRejected(413, f"Archivo de {len(document) / 1e6:.1f} MB excede el máximo de 10 MB")
        kind = sniff(document)
        if kind is None:
            raise IngestionRejected(415, "Tipo de archivo no admitido (se aceptan PDF, JPG y PNG)")
        content_type, ext = kind
        key = f"originals/{sha[:2]}/{sha}.{ext}"
        user = metadata.get("uploaded_by", "desconocido")
        self.storage.put(key, document, {"content_type": content_type, "source_name": metadata.get("source_name"), "uploaded_by": user})

        state, cause, dup_of = DocumentState.CARGADO, None, None
        bkey = metadata.get("business_key")
        if bkey and not metadata.get("confirm_duplicate"):
            active = [d for d in self.repo.find_documents_by_business_key(bkey) if d.state != DocumentState.ERROR]
            if active:
                state = DocumentState.DUPLICADO_PENDIENTE
                dup_of = active[0].id
                cause = f"Mismo equipo, fecha y guardia que el documento {dup_of[:8]}; requiere confirmación explícita"
        doc = Document(
            id=new_id(), sha256=sha, original_key=key, source_name=str(metadata.get("source_name", "")), uploaded_by=user,
            uploaded_at=now(), state=state, content_type=content_type, business_key=bkey, state_cause=cause, duplicate_of=dup_of,
            metadata={k: v for k, v in metadata.items() if k not in ("business_key", "uploaded_by", "source_name")},
        )
        with self.repo.transaction():
            self.repo.add_document(doc)
            self.repo.record_state_change(doc.id, None, state.value, cause, user)
            self.repo.add_audit(AuditEvent("document_uploaded", "document", doc.id, user, after={"sha256": sha, "state": state.value}))
        self.last_outcome = "duplicado_pendiente" if dup_of else "nuevo"
        self.telemetry.event("ingesta", document_id=doc.id, state=state.value)
        return doc.id

    def confirm_duplicate(self, doc_id: str, user: str) -> None:
        doc = self.repo.get_document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        self._move(doc, DocumentState.CARGADO, f"Duplicado confirmado por {user}", user)

    def enqueue(self, doc_id: str, user: str = "sistema") -> str | None:
        doc = self.repo.get_document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        if doc.state not in (DocumentState.CARGADO, DocumentState.ERROR, DocumentState.EN_REVISION):
            return None
        self._move(doc, DocumentState.EN_COLA, None, user)
        return self.queue.enqueue("process_document", {"document_id": doc_id})

    def _move(self, doc: Document, target: DocumentState, cause: str | None, actor: str) -> None:
        prev = doc.state
        doc.state = transition(doc.state, target)
        doc.state_cause = cause
        with self.repo.transaction():
            self.repo.update_document(doc)
            self.repo.record_state_change(doc.id, prev.value, target.value, cause, actor)
