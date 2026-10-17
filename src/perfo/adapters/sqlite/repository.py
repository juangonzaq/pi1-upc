"""RepositoryPort implementation with SQLAlchemy (SQLite in this stage, PostgreSQL-compatible schema)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from perfo.adapters.sqlite import models as m
from perfo.domain.entities import (
    Alias,
    Approval,
    AuditEvent,
    Correction,
    Crop,
    Document,
    FieldValue,
    HomologationResult,
    MasterItem,
    MasterVersion,
    Page,
    Recognition,
    ValidatedRecord,
    ValidatedValue,
)
from perfo.domain.states import DocumentState
from perfo.ports.repository import RepositoryPort


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class SqlRepository(RepositoryPort):
    def __init__(self, url: str):
        self.engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

        m.Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)
        self._tx: Session | None = None

    # ------------------------------------------------------------ sessions
    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._tx is not None:
            yield
            return
        session = self._factory()
        self._tx = session
        try:
            yield
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            self._tx = None
            session.close()

    @contextmanager
    def _s(self) -> Iterator[Session]:
        if self._tx is not None:
            yield self._tx
            self._tx.flush()
            return
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------ documents
    @staticmethod
    def _doc(r: m.DocumentRow) -> Document:
        return Document(
            id=r.id,
            sha256=r.sha256,
            original_key=r.original_key,
            source_name=r.source_name,
            uploaded_by=r.uploaded_by,
            uploaded_at=_utc(r.uploaded_at),  # type: ignore[arg-type]
            state=DocumentState(r.state),
            content_type=r.content_type,
            business_key=r.business_key,
            state_cause=r.state_cause,
            duplicate_of=r.duplicate_of,
            template_version=r.template_version,
            preprocessed_key=r.preprocessed_key,
            transform=r.transform,
            metadata=r.metadata_json or {},
        )

    def add_document(self, doc: Document) -> None:
        with self._s() as s:
            s.add(
                m.DocumentRow(
                    id=doc.id,
                    sha256=doc.sha256,
                    original_key=doc.original_key,
                    content_type=doc.content_type,
                    source_name=doc.source_name,
                    uploaded_by=doc.uploaded_by,
                    uploaded_at=doc.uploaded_at,
                    business_key=doc.business_key,
                    state=doc.state.value,
                    state_cause=doc.state_cause,
                    duplicate_of=doc.duplicate_of,
                    template_version=doc.template_version,
                    preprocessed_key=doc.preprocessed_key,
                    transform=doc.transform,
                    metadata_json=doc.metadata,
                )
            )

    def update_document(self, doc: Document) -> None:
        with self._s() as s:
            r = s.get(m.DocumentRow, doc.id)
            if r is None:
                raise KeyError(doc.id)
            r.state = doc.state.value
            r.state_cause = doc.state_cause
            r.duplicate_of = doc.duplicate_of
            r.template_version = doc.template_version
            r.preprocessed_key = doc.preprocessed_key
            r.transform = doc.transform
            r.business_key = doc.business_key
            r.metadata_json = dict(doc.metadata)

    def get_document(self, doc_id: str) -> Document | None:
        with self._s() as s:
            r = s.get(m.DocumentRow, doc_id)
            return self._doc(r) if r else None

    def find_document_by_hash(self, sha256: str) -> Document | None:
        with self._s() as s:
            r = s.scalars(select(m.DocumentRow).where(m.DocumentRow.sha256 == sha256).order_by(m.DocumentRow.uploaded_at)).first()
            return self._doc(r) if r else None

    def find_documents_by_business_key(self, key: str) -> list[Document]:
        with self._s() as s:
            rows = s.scalars(select(m.DocumentRow).where(m.DocumentRow.business_key == key)).all()
            return [self._doc(r) for r in rows]

    def list_documents(self, states: list[str] | None = None, **filters: Any) -> list[Document]:
        with self._s() as s:
            q = select(m.DocumentRow)
            if states:
                q = q.where(m.DocumentRow.state.in_(states))
            if "business_key_prefix" in filters:
                q = q.where(m.DocumentRow.business_key.like(f"{filters['business_key_prefix']}%"))
            if "batch" in filters:
                q = q.where(m.DocumentRow.metadata_json["batch"].as_string() == filters["batch"])
            q = q.order_by(m.DocumentRow.uploaded_at, m.DocumentRow.id)
            return [self._doc(r) for r in s.scalars(q).all()]

    def record_state_change(self, doc_id: str, from_state: str | None, to_state: str, cause: str | None, actor: str) -> None:
        with self._s() as s:
            s.add(
                m.DocumentStateEventRow(
                    document_id=doc_id, from_state=from_state, to_state=to_state, cause=cause, actor=actor, at=datetime.now(UTC)
                )
            )

    def state_history(self, doc_id: str) -> list[dict[str, Any]]:
        with self._s() as s:
            rows = s.scalars(
                select(m.DocumentStateEventRow).where(m.DocumentStateEventRow.document_id == doc_id).order_by(m.DocumentStateEventRow.id)
            ).all()
            return [
                {"from": r.from_state, "to": r.to_state, "cause": r.cause, "actor": r.actor, "at": _utc(r.at).isoformat()}  # type: ignore[union-attr]
                for r in rows
            ]

    # ------------------------------------------------------------ pages/templates/crops
    def add_page(self, page: Page) -> None:
        with self._s() as s:
            existing = s.scalars(
                select(m.PageRow).where(m.PageRow.document_id == page.document_id, m.PageRow.page_no == page.page_no)
            ).first()
            if existing:
                existing.object_key = page.object_key
                existing.width, existing.height, existing.dpi_equivalent = page.width, page.height, page.dpi_equivalent
                page.id = existing.id
                return
            s.add(m.PageRow(**asdict(page)))

    def get_pages(self, doc_id: str) -> list[Page]:
        with self._s() as s:
            rows = s.scalars(select(m.PageRow).where(m.PageRow.document_id == doc_id).order_by(m.PageRow.page_no)).all()
            return [
                Page(id=r.id, document_id=r.document_id, page_no=r.page_no, object_key=r.object_key, width=r.width, height=r.height, dpi_equivalent=r.dpi_equivalent)
                for r in rows
            ]

    def save_template(self, template_id: str, version: str, definition: dict[str, Any], sha256: str) -> None:
        with self._s() as s:
            r = s.scalars(select(m.TemplateRow).where(m.TemplateRow.template_id == template_id, m.TemplateRow.version == version)).first()
            if r is not None:
                if r.sha256 != sha256:
                    raise ValueError(
                        f"La plantilla {template_id} v{version} ya existe con otro contenido; publique una nueva versión"
                    )
                return
            s.add(m.TemplateRow(template_id=template_id, version=version, sha256=sha256, definition=definition, created_at=datetime.now(UTC)))

    def get_template_definition(self, template_id: str, version: str) -> dict[str, Any] | None:
        with self._s() as s:
            r = s.scalars(select(m.TemplateRow).where(m.TemplateRow.template_id == template_id, m.TemplateRow.version == version)).first()
            return r.definition if r else None

    @staticmethod
    def _crop(r: m.CropRow) -> Crop:
        return Crop(
            id=r.id,
            document_id=r.document_id,
            page_id=r.page_id,
            field_id=r.field_id,
            template_version=r.template_version,
            box_px=tuple(r.box_px),  # type: ignore[arg-type]
            object_key=r.object_key,
            sha256=r.sha256,
            is_empty=r.is_empty,
            ink_ratio=r.ink_ratio,
            mark=r.mark,
        )

    def upsert_crops(self, crops: list[Crop]) -> list[Crop]:
        out: list[Crop] = []
        with self._s() as s:
            if not crops:
                return out
            doc_id = crops[0].document_id
            existing = {
                (r.field_id, r.template_version): r
                for r in s.scalars(select(m.CropRow).where(m.CropRow.document_id == doc_id)).all()
            }
            for c in crops:
                r = existing.get((c.field_id, c.template_version))
                if r is None:
                    r = m.CropRow(
                        id=c.id,
                        document_id=c.document_id,
                        page_id=c.page_id,
                        field_id=c.field_id,
                        template_version=c.template_version,
                        box_px=list(c.box_px),
                        object_key=c.object_key,
                        sha256=c.sha256,
                        is_empty=c.is_empty,
                        ink_ratio=c.ink_ratio,
                        mark=c.mark,
                    )
                    s.add(r)
                else:
                    # reprocess: keep the same crop id; content is deterministic
                    r.box_px, r.object_key, r.sha256 = list(c.box_px), c.object_key, c.sha256
                    r.is_empty, r.ink_ratio, r.mark, r.page_id = c.is_empty, c.ink_ratio, c.mark, c.page_id
                out.append(self._crop(r))
        return out

    def get_crops(self, doc_id: str) -> list[Crop]:
        with self._s() as s:
            rows = s.scalars(select(m.CropRow).where(m.CropRow.document_id == doc_id)).all()
            return [self._crop(r) for r in rows]

    def get_crop(self, crop_id: str) -> Crop | None:
        with self._s() as s:
            r = s.get(m.CropRow, crop_id)
            return self._crop(r) if r else None

    # ------------------------------------------------------------ recognition / field values
    def add_recognitions(self, recs: list[Recognition]) -> None:
        with self._s() as s:
            s.add_all([m.RecognitionRow(**asdict(r)) for r in recs])

    def get_recognitions(self, crop_ids: list[str], model_version: str | None = None) -> list[Recognition]:
        with self._s() as s:
            out: list[Recognition] = []
            for i in range(0, len(crop_ids), 500):
                q = select(m.RecognitionRow).where(m.RecognitionRow.crop_id.in_(crop_ids[i : i + 500]))
                if model_version:
                    q = q.where(m.RecognitionRow.model_version == model_version)
                for r in s.scalars(q).all():
                    out.append(
                        Recognition(
                            id=r.id, crop_id=r.crop_id, model_version=r.model_version, text=r.text, score=r.score,
                            raw_score=r.raw_score, reliable=r.reliable, calibration_id=r.calibration_id,
                            latency_ms=r.latency_ms, created_at=_utc(r.created_at),  # type: ignore[arg-type]
                        )
                    )
            return out

    def add_field_values(self, values: list[FieldValue]) -> None:
        now = datetime.now(UTC)
        with self._s() as s:
            for seq, v in enumerate(values):
                s.add(
                    m.FieldValueRow(
                        id=v.id, document_id=v.document_id, run_id=v.run_id, seq=seq, field_id=v.field_id,
                        field_type=v.field_type, crop_id=v.crop_id, recognition_id=v.recognition_id, text=v.text,
                        value=v.value, confidence=v.confidence, route=v.route, reasons=list(v.reasons),
                        violations=list(v.violations), mark=v.mark, created_at=now,
                    )
                )
                if v.homologation is not None:
                    h = v.homologation
                    s.add(
                        m.HomologationRow(
                            field_value_id=v.id, master_type=h.master_type, master_version=h.master_version,
                            input_text=h.input_text, normalized=h.normalized, code=h.code, score=h.score,
                            method=h.method, accepted=h.accepted, candidates=h.candidates,
                        )
                    )

    def _fv(self, s: Session, r: m.FieldValueRow) -> FieldValue:
        h = s.scalars(select(m.HomologationRow).where(m.HomologationRow.field_value_id == r.id)).first()
        hom = None
        if h is not None:
            hom = HomologationResult(
                master_type=h.master_type, master_version=h.master_version, input_text=h.input_text,
                normalized=h.normalized, code=h.code, score=h.score, method=h.method, accepted=h.accepted,
                candidates=h.candidates,
            )
        return FieldValue(
            id=r.id, document_id=r.document_id, run_id=r.run_id, field_id=r.field_id, field_type=r.field_type,
            crop_id=r.crop_id, recognition_id=r.recognition_id, text=r.text, value=r.value, confidence=r.confidence,
            route=r.route, reasons=list(r.reasons), violations=list(r.violations), homologation=hom, mark=r.mark,
        )

    def get_field_values(self, doc_id: str, run_id: str | None = None) -> list[FieldValue]:
        with self._s() as s:
            if run_id is None:
                last = s.scalars(
                    select(m.FieldValueRow).where(m.FieldValueRow.document_id == doc_id).order_by(m.FieldValueRow.created_at.desc())
                ).first()
                if last is None:
                    return []
                run_id = last.run_id
            rows = s.scalars(
                select(m.FieldValueRow).where(m.FieldValueRow.document_id == doc_id, m.FieldValueRow.run_id == run_id).order_by(m.FieldValueRow.seq)
            ).all()
            return [self._fv(s, r) for r in rows]

    def get_field_value(self, fv_id: str) -> FieldValue | None:
        with self._s() as s:
            r = s.get(m.FieldValueRow, fv_id)
            return self._fv(s, r) if r else None

    # ------------------------------------------------------------ validation
    def add_validated_record(self, record: ValidatedRecord) -> None:
        with self._s() as s:
            s.add(
                m.ValidatedRecordRow(
                    id=record.id, document_id=record.document_id, version=record.version, validated_by=record.validated_by,
                    validated_at=record.validated_at, sha256=record.sha256, supersedes=record.supersedes, stats=record.stats,
                )
            )
            s.flush()
            s.add_all(
                [
                    m.ValidatedValueRow(record_id=record.id, field_id=v.field_id, value=v.value, source=v.source, field_value_id=v.field_value_id)
                    for v in record.values
                ]
            )

    def get_records(self, doc_id: str) -> list[ValidatedRecord]:
        with self._s() as s:
            rows = s.scalars(
                select(m.ValidatedRecordRow).where(m.ValidatedRecordRow.document_id == doc_id).order_by(m.ValidatedRecordRow.version)
            ).all()
            out = []
            for r in rows:
                vals = s.scalars(select(m.ValidatedValueRow).where(m.ValidatedValueRow.record_id == r.id).order_by(m.ValidatedValueRow.id)).all()
                out.append(
                    ValidatedRecord(
                        id=r.id, document_id=r.document_id, version=r.version, validated_by=r.validated_by,
                        validated_at=_utc(r.validated_at), sha256=r.sha256, supersedes=r.supersedes, stats=r.stats,  # type: ignore[arg-type]
                        values=[ValidatedValue(field_id=v.field_id, value=v.value, source=v.source, field_value_id=v.field_value_id) for v in vals],
                    )
                )
            return out

    def add_corrections(self, corrections: list[Correction]) -> None:
        with self._s() as s:
            s.add_all([m.CorrectionRow(**asdict(c)) for c in corrections])

    def list_corrections(self, unused_only: bool = True, kinds: list[str] | None = None) -> list[Correction]:
        with self._s() as s:
            q = select(m.CorrectionRow)
            if unused_only:
                q = q.where(m.CorrectionRow.snapshot_id.is_(None))
            if kinds:
                q = q.where(m.CorrectionRow.kind.in_(kinds))
            rows = s.scalars(q.order_by(m.CorrectionRow.created_at, m.CorrectionRow.id)).all()
            out = []
            for r in rows:
                d = {c.name: getattr(r, c.name) for c in m.CorrectionRow.__table__.columns}
                d["created_at"] = _utc(d["created_at"])
                out.append(Correction(**d))
            return out

    def mark_corrections_used(self, ids: list[str], snapshot_id: str) -> None:
        with self._s() as s:
            for i in range(0, len(ids), 500):
                s.execute(update(m.CorrectionRow).where(m.CorrectionRow.id.in_(ids[i : i + 500])).values(snapshot_id=snapshot_id))

    def add_approval(self, approval: Approval) -> None:
        with self._s() as s:
            s.add(m.ApprovalRow(**asdict(approval)))

    def get_approvals(self, doc_id: str) -> list[Approval]:
        with self._s() as s:
            rows = s.scalars(select(m.ApprovalRow).where(m.ApprovalRow.document_id == doc_id).order_by(m.ApprovalRow.created_at)).all()
            return [
                Approval(id=r.id, record_id=r.record_id, document_id=r.document_id, decision=r.decision, comment=r.comment,
                         approver=r.approver, created_at=_utc(r.created_at))  # type: ignore[arg-type]
                for r in rows
            ]

    # ------------------------------------------------------------ masters
    def save_master_version(self, mv: MasterVersion) -> None:
        with self._s() as s:
            r = m.MasterVersionRow(
                master_type=mv.master_type, version=mv.version, imported_at=mv.imported_at, source_name=mv.source_name,
                sha256=mv.sha256, status=mv.status, errors=mv.errors,
            )
            s.add(r)
            s.flush()
            s.add_all(
                [
                    m.MasterItemRow(master_version_id=r.id, code=i.code, description=i.description, extra=i.extra, representations=i.representations)
                    for i in mv.items
                ]
            )

    def _mv(self, s: Session, r: m.MasterVersionRow) -> MasterVersion:
        items = s.scalars(select(m.MasterItemRow).where(m.MasterItemRow.master_version_id == r.id).order_by(m.MasterItemRow.id)).all()
        return MasterVersion(
            master_type=r.master_type, version=r.version, imported_at=_utc(r.imported_at), source_name=r.source_name,  # type: ignore[arg-type]
            sha256=r.sha256, status=r.status, errors=r.errors,
            items=[MasterItem(code=i.code, description=i.description, extra=i.extra, representations=i.representations) for i in items],
        )

    def get_master_version(self, master_type: str, version: int | None = None) -> MasterVersion | None:
        with self._s() as s:
            q = select(m.MasterVersionRow).where(m.MasterVersionRow.master_type == master_type)
            q = q.where(m.MasterVersionRow.version == version) if version is not None else q.where(m.MasterVersionRow.status == "active")
            r = s.scalars(q).first()
            return self._mv(s, r) if r else None

    def find_master_by_hash(self, master_type: str, sha256: str) -> MasterVersion | None:
        with self._s() as s:
            r = s.scalars(
                select(m.MasterVersionRow).where(
                    m.MasterVersionRow.master_type == master_type, m.MasterVersionRow.sha256 == sha256, m.MasterVersionRow.status != "rejected"
                )
            ).first()
            return self._mv(s, r) if r else None

    def next_master_version(self, master_type: str) -> int:
        with self._s() as s:
            v = s.scalar(select(func.max(m.MasterVersionRow.version)).where(m.MasterVersionRow.master_type == master_type))
            return int(v or 0) + 1

    def set_master_status(self, master_type: str, version: int, status: str) -> None:
        with self._s() as s:
            s.execute(
                update(m.MasterVersionRow)
                .where(m.MasterVersionRow.master_type == master_type, m.MasterVersionRow.version == version)
                .values(status=status)
            )

    def upsert_alias(self, alias: Alias) -> Alias:
        with self._s() as s:
            r = s.scalars(select(m.AliasRow).where(m.AliasRow.master_type == alias.master_type, m.AliasRow.alias == alias.alias)).first()
            if r is None:
                s.add(m.AliasRow(**asdict(alias)))
            else:
                r.code, r.created_by, r.created_at, r.source, r.needs_review = alias.code, alias.created_by, alias.created_at, alias.source, False
            return alias

    def get_aliases(self, master_type: str) -> list[Alias]:
        with self._s() as s:
            rows = s.scalars(select(m.AliasRow).where(m.AliasRow.master_type == master_type).order_by(m.AliasRow.id)).all()
            return [
                Alias(master_type=r.master_type, alias=r.alias, code=r.code, created_by=r.created_by,
                      created_at=_utc(r.created_at), source=r.source, needs_review=r.needs_review)  # type: ignore[arg-type]
                for r in rows
            ]

    def mark_aliases_for_review(self, master_type: str, valid_codes: set[str]) -> int:
        n = 0
        with self._s() as s:
            for r in s.scalars(select(m.AliasRow).where(m.AliasRow.master_type == master_type)).all():
                flag = r.code not in valid_codes
                if flag and not r.needs_review:
                    n += 1
                r.needs_review = flag
        return n

    # ------------------------------------------------------------ models / datasets
    def upsert_model_version(self, info: dict[str, Any]) -> None:
        with self._s() as s:
            r = s.get(m.ModelVersionRow, info["version"])
            fields = {k: info.get(k) for k in ("base_checkpoint", "parent", "status", "artifact_uri", "artifact_sha256", "created_at")}
            if r is None:
                s.add(m.ModelVersionRow(version=info["version"], info=info.get("metadata", {}), **fields))
            else:
                for k, v in fields.items():
                    setattr(r, k, v)
                r.info = info.get("metadata", {})

    def add_calibration_profile(self, profile: dict[str, Any]) -> None:
        with self._s() as s:
            if s.get(m.CalibrationProfileRow, profile["id"]) is None:
                s.add(
                    m.CalibrationProfileRow(
                        id=profile["id"], model_version=profile["model_version"], aggregation=profile["aggregation"],
                        profile=profile, created_at=profile["created_at"],
                    )
                )

    def save_split(self, version: str, manifest: dict[str, Any], frozen: bool) -> None:
        with self._s() as s:
            r = s.get(m.SplitRow, version)
            if r is not None:
                if r.frozen:
                    raise ValueError(f"La partición {version} está congelada y no puede modificarse")
                r.manifest, r.frozen = manifest, frozen
            else:
                s.add(m.SplitRow(version=version, manifest=manifest, frozen=frozen, created_at=datetime.now(UTC)))

    def get_split(self, version: str | None = None) -> dict[str, Any] | None:
        with self._s() as s:
            if version:
                r = s.get(m.SplitRow, version)
            else:
                r = s.scalars(select(m.SplitRow).order_by(m.SplitRow.created_at.desc())).first()
            if r is None:
                return None
            return {"version": r.version, "frozen": r.frozen, **r.manifest}

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self._s() as s:
            s.add(
                m.SnapshotRow(
                    id=snapshot["id"], kind=snapshot["kind"], manifest_key=snapshot["manifest_key"],
                    manifest_sha256=snapshot["manifest_sha256"], counts=snapshot.get("counts", {}), created_at=datetime.now(UTC),
                )
            )

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._s() as s:
            rows = s.scalars(select(m.SnapshotRow).order_by(m.SnapshotRow.created_at)).all()
            return [
                {"id": r.id, "kind": r.kind, "manifest_key": r.manifest_key, "manifest_sha256": r.manifest_sha256, "counts": r.counts}
                for r in rows
            ]

    # ------------------------------------------------------------ audit
    def add_audit(self, event: AuditEvent) -> None:
        with self._s() as s:
            s.add(
                m.AuditRow(
                    at=event.at, actor=event.actor, action=event.action, entity=event.entity, entity_id=event.entity_id,
                    before=event.before, after=event.after, correlation_id=event.correlation_id,
                )
            )
