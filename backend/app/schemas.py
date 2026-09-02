import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from app.models import DocumentStatus, DocumentType, ExtractionMethod, OrgRole, ReviewStatus


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
    field_name: str
    value: str | None
    confidence: Decimal
    method: ExtractionMethod

    model_config = {"from_attributes": True}


class TransactionOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    vendor: str | None
    amount: Decimal | None
    transaction_date: date | None
    confidence: Decimal | None
    review_status: ReviewStatus

    model_config = {"from_attributes": True}
