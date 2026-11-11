import uuid

from litestar import Router, get, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.params import FromPath, JSONBody
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_ENTITY,
)
from sqlalchemy.orm import Session

from app.auth import Principal
from app.celery_app import process_document
from app.documents import validate_upload
from app.models import Document, DocumentStatus
from app.schemas import DocumentOut, DocumentUploadRequest, DocumentUploadResponse
from app.scoping import org_scoped_select
from app.storage import presigned_put_url


@post("/", status_code=HTTP_201_CREATED, sync_to_thread=True)
def request_upload(
    data: JSONBody[DocumentUploadRequest],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> DocumentUploadResponse:
    error = validate_upload(data.filename, data.content_type, data.size_bytes)
    if error is not None:
        raise HTTPException(status_code=HTTP_422_UNPROCESSABLE_ENTITY, detail=error)

    minio_key = f"{principal.org_id}/{uuid.uuid4()}-{data.filename}"

    document = Document(
        org_id=principal.org_id,
        filename=data.filename,
        content_type=data.content_type,
        size_bytes=data.size_bytes,
        minio_key=minio_key,
        doc_type=data.doc_type,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    upload_url = presigned_put_url(minio_key)

    return DocumentUploadResponse(document=DocumentOut.model_validate(document), upload_url=upload_url)


@post("/{document_id:uuid}/complete", status_code=HTTP_200_OK, sync_to_thread=True)
def complete_upload(
    document_id: FromPath[uuid.UUID],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> DocumentOut:
    document = (
        db.execute(org_scoped_select(Document, principal.org_id).where(Document.id == document_id))
        .scalars()
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Document not found")
    if document.status != DocumentStatus.queued:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT, detail="Upload already completed for this document"
        )

    process_document.delay(str(document.id))
    db.refresh(document)
    return DocumentOut.model_validate(document)


@get("/{document_id:uuid}", sync_to_thread=True)
def get_document(
    document_id: FromPath[uuid.UUID],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> DocumentOut:
    document = (
        db.execute(org_scoped_select(Document, principal.org_id).where(Document.id == document_id))
        .scalars()
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Document not found")
    return DocumentOut.model_validate(document)


router = Router(
    path="/documents",
    route_handlers=[request_upload, complete_upload, get_document],
    tags=["documents"],
)
