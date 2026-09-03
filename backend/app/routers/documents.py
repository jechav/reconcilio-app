import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.celery_app import process_document
from app.database import get_db
from app.deps import Principal, get_current_principal
from app.documents import validate_upload
from app.models import Document, DocumentStatus
from app.schemas import DocumentOut, DocumentUploadRequest, DocumentUploadResponse
from app.scoping import org_scoped_select
from app.storage import presigned_put_url

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
def request_upload(
    payload: DocumentUploadRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentUploadResponse:
    error = validate_upload(payload.filename, payload.content_type, payload.size_bytes)
    if error is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, error)

    minio_key = f"{principal.org_id}/{uuid.uuid4()}-{payload.filename}"

    document = Document(
        org_id=principal.org_id,
        filename=payload.filename,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        minio_key=minio_key,
        doc_type=payload.doc_type,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    upload_url = presigned_put_url(minio_key)

    return DocumentUploadResponse(document=DocumentOut.model_validate(document), upload_url=upload_url)


@router.post("/{document_id}/complete", response_model=DocumentOut)
def complete_upload(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Document:
    document = (
        db.execute(org_scoped_select(Document, principal.org_id).where(Document.id == document_id))
        .scalars()
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if document.status != DocumentStatus.queued:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload already completed for this document")

    process_document.delay(str(document.id))
    db.refresh(document)
    return document


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Document:
    document = (
        db.execute(org_scoped_select(Document, principal.org_id).where(Document.id == document_id))
        .scalars()
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document
