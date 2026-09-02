import enum
import uuid
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_CONFIDENCE_THRESHOLD = 0.8


class OrgRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Fields extracted below this confidence are refined by the LLM pass and,
    # if still low afterwards, flagged for human review. Owner-configurable.
    confidence_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_CONFIDENCE_THRESHOLD, server_default="0.8"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    memberships: Mapped[list["OrgMembership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    memberships: Mapped[list["OrgMembership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class OrgMembership(Base):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id", name="uq_org_memberships_user_org"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    role: Mapped[OrgRole] = mapped_column(Enum(OrgRole, name="org_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    user: Mapped["User"] = relationship(back_populates="memberships")
    organization: Mapped["Organization"] = relationship(back_populates="memberships")


class DocumentType(str, enum.Enum):
    invoice_or_receipt = "invoice_or_receipt"
    bank_statement = "bank_statement"


class DocumentStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    needs_review = "needs_review"
    done = "done"
    failed = "failed"


class TransactionStatus(str, enum.Enum):
    """Review state of a single extracted line.

    `resolved` means nothing about the line needs a human: every field came
    back at or above the Organization's confidence threshold (or from a
    structured parse, which is exact). `needs_review` is the flag a human
    clears in the review UI.
    """

    needs_review = "needs_review"
    resolved = "resolved"


class ExtractionMethod(str, enum.Enum):
    """Which mechanism produced a field/line -- the audit-trail provenance.

    `structured_parse` is CSV/OFX, which is machine-readable to begin with
    and therefore always confidence 1.0 (see CONTEXT.md, ExtractionResult).
    """

    ocr = "ocr"
    llm = "llm"
    structured_parse = "structured_parse"


class Document(Base):
    """A source file uploaded by a user (invoice, receipt, or bank statement).

    Stored in MinIO under `minio_key`; `status` tracks progress through the
    (currently stub) extraction pipeline. See CONTEXT.md for the full
    domain definition.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    minio_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    doc_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType, name="document_type"), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"), nullable=False, default=DocumentStatus.queued
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Transaction.line_number"
    )


class Transaction(Base):
    """One normalized line item extracted from a Document.

    An invoice/receipt Document yields exactly one; a bank statement yields
    one per statement line (`line_number` preserves statement order). Amount
    sign carries direction: negative is money out, positive money in.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("document_id", "line_number", name="uq_transactions_document_line"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    txn_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="transactions")
    extraction_results: Mapped[list["ExtractionResult"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


class ExtractionResult(Base):
    """Raw per-field extraction output for one line of one Document.

    `fields` is `{field_name: {value, confidence, method}}` so the origin of
    every single field is recoverable regardless of ingestion path; `method`
    and `confidence` on the row itself summarise the line as a whole (the
    weakest field's method/confidence).
    """

    __tablename__ = "extraction_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    line_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[ExtractionMethod] = mapped_column(
        Enum(ExtractionMethod, name="extraction_method"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    transaction: Mapped["Transaction | None"] = relationship(back_populates="extraction_results")


class AuditLogEntry(Base):
    """An append-only record of who changed what, when.

    Pipeline actions record actor `system`; user edits record the acting
    user's id (see CONTEXT.md, Manual match).
    """

    __tablename__ = "audit_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


SYSTEM_ACTOR = "system"
