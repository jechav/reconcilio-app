import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from app.models import DocumentStatus, DocumentType, ExtractionMethod, MatchType, OrgRole, TransactionStatus


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    org_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AcceptInviteRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class InviteRequest(BaseModel):
    email: EmailStr
    role: OrgRole


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    confidence_threshold: Decimal

    model_config = {"from_attributes": True}


class OrgSettingsUpdate(BaseModel):
    confidence_threshold: Decimal = Field(gt=0, le=1)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    organization: OrganizationOut
    role: OrgRole


class MembershipOut(BaseModel):
    id: uuid.UUID
    user: UserOut
    role: OrgRole
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    doc_type: DocumentType
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    doc_type: DocumentType


class DocumentUploadResponse(BaseModel):
    document: DocumentOut
    upload_url: str


class ExtractionResultOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    transaction_id: uuid.UUID | None
    line_number: int
    method: ExtractionMethod
    confidence: float
    fields: dict[str, object]

    model_config = {"from_attributes": True}


class TransactionOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    line_number: int
    description: str
    amount: Decimal
    txn_date: date
    confidence: float
    status: TransactionStatus
    category_id: uuid.UUID | None
    category_confidence: float | None

    model_config = {"from_attributes": True}


class ReconciliationMatchOut(BaseModel):
    id: uuid.UUID
    bank_transaction_id: uuid.UUID
    expense_transaction_id: uuid.UUID
    match_type: MatchType
    confidence: float
    actor: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LlmUsageOut(BaseModel):
    """One (provider, model) total for the calling Organization -- see
    app/llm_usage.py (issue #7, AC5)."""

    provider: str
    model: str
    calls: int


class ManualMatchRequest(BaseModel):
    bank_transaction_id: uuid.UUID
    expense_transaction_id: uuid.UUID


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CategoryUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class TransactionCategoryCorrectionRequest(BaseModel):
    category_id: uuid.UUID


class CategorySummaryOut(BaseModel):
    """One Category's income/expense picture for a dashboard date range
    (issue #8, AC1). `category_id` is `None` for Transactions with no
    Category assigned -- grouped under `category_name` "Uncategorized"
    rather than dropped."""

    category_id: uuid.UUID | None
    category_name: str
    income: Decimal
    expenses: Decimal
    transaction_count: int


class DashboardSummaryOut(BaseModel):
    """Cash-basis income/expense summary grouped by Category, for an
    arbitrary user-selected date range (issue #8, AC1/AC2). `start_date`/
    `end_date` are ad-hoc filters, not a stored Period (see CONTEXT.md,
    Period)."""

    start_date: date
    end_date: date
    income_total: Decimal
    expenses_total: Decimal
    net_total: Decimal
    categories: list[CategorySummaryOut]


class DashboardFlagsOut(BaseModel):
    """Missing-documentation flags for a date range: bank/expense-source
    Transactions that never reconciled (issue #8, AC3). Each Transaction
    carries `document_id`, letting a caller drill down to the source
    Document (AC4)."""

    start_date: date
    end_date: date
    unmatched_bank_transactions: list[TransactionOut]
    unmatched_expense_transactions: list[TransactionOut]


class TransactionExportRow(BaseModel):
    """One Transaction rendered for an accountant/tax-software export
    (issue #9). Every Transaction in the selected date range is included
    regardless of processing state -- `category`, `review_status`, and
    `match_status` make the still-needs-attention rows explicit rather than
    silently dropping them (AC3).

    `category` is the Category name (or "Uncategorized"), not an id, since
    the destination is a human/accountant-facing table, not another API
    consumer. `review_status` mirrors Transaction.status (CONTEXT.md:
    `needs_review` clears once a human confirms the extracted fields).
    `match_status` is derived from ReconciliationMatch membership --
    `matched` or `unmatched` -- since a Transaction carries no match state
    of its own (see CONTEXT.md, ReconciliationMatch)."""

    id: uuid.UUID
    document_id: uuid.UUID
    txn_date: date
    description: str
    amount: Decimal
    category: str
    review_status: TransactionStatus
    match_status: str

    model_config = {"from_attributes": True}
