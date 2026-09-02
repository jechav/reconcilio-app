"""Extraction pipeline: classify -> ocr_extract -> llm_refine -> validate ->
categorize -> persist.

Issue #2 built this graph's shape with no-op stub nodes so the async
plumbing (upload -> Celery job -> pipeline -> Document.status transitions)
could be proven end to end. Issue #3 fills in real behavior for the
invoice/receipt path:

- `classify`: confirms the declared Document.doc_type against the file's
  actual content (app/extraction/classify.py); an unrecognized document is
  flagged rather than silently misprocessed.
- `ocr_extract`: AWS Textract's AnalyzeExpense API produces per-field
  values with confidence scores (app/extraction/textract.py).
- `llm_refine`: any field below the Organization's confidence_threshold
  gets a second pass from a vision-capable LLM (app/extraction/llm.py).
  Every field's origin (`ocr` vs `llm`) and confidence is recorded
  uniformly regardless of path.
- `validate`: the collected fields must satisfy `ExtractionSchema`
  (app/extraction/schema.py) before persisting; a field that fails to
  parse is treated as zero-confidence rather than persisted as-is.
- `categorize`: still a stub -- Category assignment is a later ticket.
- `persist`: writes one ExtractionResult per field, exactly one
  Transaction, and an AuditLogEntry. A field still below threshold after
  refinement flags the Transaction for human review rather than being
  silently persisted as confident.

`categorize` and the bank-statement extraction path (issue #4) remain
no-ops here; this ticket only "establishes the pattern" per the issue body.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.extraction.classify import STRUCTURED_CONTENT_TYPES, detect_document_type
from app.extraction.llm import LLMRefinementClient, get_llm_client
from app.extraction.schema import ExtractionSchema
from app.extraction.textract import FIELD_NAMES, TextractClient, get_textract_client
from app.models import (
    AuditLogEntry,
    Document,
    DocumentStatus,
    DocumentType,
    ExtractionMethod,
    ExtractionResult,
    Organization,
    ReviewStatus,
    Transaction,
)
from app.storage import get_object_bytes


class FieldRecord(TypedDict):
    value: str | None
    confidence: float
    method: ExtractionMethod


class PipelineState(TypedDict, total=False):
    document_id: str
    document_bytes: bytes
    detected_type: str | None  # DocumentType.value, or "unknown"
    extracted_fields: dict[str, FieldRecord]
    validated: dict[str, Any]


def _build_graph(
    db: Session,
    textract_client: TextractClient,
    llm_client: LLMRefinementClient,
):
    def classify(state: PipelineState) -> PipelineState:
        document = db.get(Document, uuid.UUID(state["document_id"]))
        assert document is not None

        if document.content_type in STRUCTURED_CONTENT_TYPES:
            # File format itself is the classification signal for
            # CSV/OFX bank statements; no OCR pass needed.
            state["detected_type"] = document.doc_type.value
            return state

        document_bytes = get_object_bytes(document.minio_key)
        state["document_bytes"] = document_bytes

        raw_lines = textract_client.detect_text(document_bytes)
        detected = detect_document_type(raw_lines)
        state["detected_type"] = detected.value if detected is not None else None
        return state

    def ocr_extract(state: PipelineState) -> PipelineState:
        if state.get("detected_type") != DocumentType.invoice_or_receipt.value:
            # Unknown, or a bank statement (issue #4's path) -- no
            # invoice/receipt extraction to run.
            return state

        document_bytes = state.get("document_bytes")
        if document_bytes is None:
            return state

        result = textract_client.analyze_expense(document_bytes)
        fields: dict[str, FieldRecord] = {}
        for field in result.fields:
            fields[field.name] = FieldRecord(
                value=field.value, confidence=field.confidence, method=ExtractionMethod.ocr
            )
        state["extracted_fields"] = fields
        return state

    def llm_refine(state: PipelineState) -> PipelineState:
        fields = state.get("extracted_fields")
        if not fields:
            return state

        document = db.get(Document, uuid.UUID(state["document_id"]))
        assert document is not None
        org = db.get(Organization, document.org_id)
        assert org is not None
        threshold = float(org.confidence_threshold)

        document_bytes = state.get("document_bytes")
        assert document_bytes is not None

        for field_name in FIELD_NAMES:
            current = fields.get(field_name)
            current_confidence = current["confidence"] if current is not None else 0.0
            if current_confidence >= threshold:
                continue

            refined = llm_client.refine_field(
                field_name,
                document_bytes,
                document.content_type,
                current["value"] if current is not None else None,
            )
            fields[field_name] = FieldRecord(
                value=refined.value, confidence=refined.confidence, method=ExtractionMethod.llm
            )

        state["extracted_fields"] = fields
        return state

    def validate(state: PipelineState) -> PipelineState:
        fields = state.get("extracted_fields")
        if not fields:
            return state

        def _to_schema(values: dict[str, str | None]) -> ExtractionSchema:
            return ExtractionSchema(
                vendor=values.get("vendor"),
                amount=values.get("amount"),  # type: ignore[arg-type]
                invoice_date=values.get("invoice_date"),  # type: ignore[arg-type]
            )

        raw = {name: fields[name]["value"] for name in fields}
        try:
            validated = _to_schema(raw)
        except ValidationError as exc:
            invalid_fields = {str(error["loc"][0]) for error in exc.errors()}
            for name in invalid_fields:
                if name in fields:
                    fields[name] = FieldRecord(
                        value=fields[name]["value"], confidence=0.0, method=fields[name]["method"]
                    )
            raw = {name: (None if name in invalid_fields else fields[name]["value"]) for name in fields}
            validated = _to_schema(raw)

        state["extracted_fields"] = fields
        state["validated"] = validated.model_dump()
        return state

    def categorize(state: PipelineState) -> PipelineState:
        # Category assignment lands in a later ticket; invoice/receipt
        # Transactions persist with no category for now.
        return state

    def persist(state: PipelineState) -> PipelineState:
        document = db.get(Document, uuid.UUID(state["document_id"]))
        assert document is not None

        detected_type = state.get("detected_type")
        if detected_type is None:
            document.status = DocumentStatus.needs_review
            db.add(
                AuditLogEntry(
                    org_id=document.org_id,
                    actor="system",
                    action="document.classification_unknown",
                    entity_type="Document",
                    entity_id=document.id,
                    before={"doc_type": document.doc_type.value},
                    after={"detected_type": None},
                )
            )
            db.commit()
            return state

        if detected_type != DocumentType.invoice_or_receipt.value:
            # Bank statement path (issue #4) -- not yet implemented here.
            db.commit()
            return state

        fields = state.get("validated") or {}
        extracted_fields: dict[str, FieldRecord] = state.get("extracted_fields") or {}
        confidences = {name: record["confidence"] for name, record in extracted_fields.items()}
        methods = {name: record["method"] for name, record in extracted_fields.items()}

        document_org_id = document.org_id
        for field_name in FIELD_NAMES:
            confidence = confidences.get(field_name, 0.0)
            method = methods.get(field_name, ExtractionMethod.ocr)
            field_record = extracted_fields.get(field_name)
            raw_value = field_record["value"] if field_record is not None else None
            db.add(
                ExtractionResult(
                    org_id=document_org_id,
                    document_id=document.id,
                    field_name=field_name,
                    value=raw_value,
                    confidence=Decimal(str(round(confidence, 3))),
                    method=method,
                )
            )

        org = db.get(Organization, document_org_id)
        assert org is not None
        threshold = float(org.confidence_threshold)
        needs_review = any(confidences.get(name, 0.0) < threshold for name in FIELD_NAMES)

        amount_value = fields.get("amount")
        try:
            amount = Decimal(str(amount_value)) if amount_value is not None else None
        except InvalidOperation:
            amount = None
            needs_review = True

        min_confidence = min((confidences.get(name, 0.0) for name in FIELD_NAMES), default=0.0)

        transaction = Transaction(
            org_id=document_org_id,
            document_id=document.id,
            vendor=fields.get("vendor"),
            amount=amount,
            transaction_date=fields.get("invoice_date"),
            confidence=Decimal(str(round(min_confidence, 3))),
            review_status=ReviewStatus.needs_review if needs_review else ReviewStatus.ok,
        )
        db.add(transaction)
        db.flush()

        db.add(
            AuditLogEntry(
                org_id=document_org_id,
                actor="system",
                action="document.extracted",
                entity_type="Transaction",
                entity_id=transaction.id,
                before=None,
                after={
                    "vendor": transaction.vendor,
                    "amount": str(transaction.amount) if transaction.amount is not None else None,
                    "transaction_date": (
                        transaction.transaction_date.isoformat()
                        if transaction.transaction_date is not None
                        else None
                    ),
                    "review_status": transaction.review_status.value,
                },
            )
        )
        db.commit()
        return state

    graph = StateGraph(PipelineState)
    graph.add_node("classify", classify)
    graph.add_node("ocr_extract", ocr_extract)
    graph.add_node("llm_refine", llm_refine)
    graph.add_node("validate", validate)
    graph.add_node("categorize", categorize)
    graph.add_node("persist", persist)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "ocr_extract")
    graph.add_edge("ocr_extract", "llm_refine")
    graph.add_edge("llm_refine", "validate")
    graph.add_edge("validate", "categorize")
    graph.add_edge("categorize", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


def run_pipeline(
    document_id: uuid.UUID,
    db: Session,
    *,
    textract_client: TextractClient | None = None,
    llm_client: LLMRefinementClient | None = None,
) -> Document:
    """Drive a Document through the pipeline: queued -> processing -> done
    (or `needs_review` if classify_document can't place it -- see AC1).

    Callable directly (no HTTP, no Celery) so it can be exercised as a
    pipeline-as-a-function unit test, and reused by the Celery task.
    `textract_client`/`llm_client` default to the real AWS/LiteLLM-backed
    clients; tests inject fakes so no live credentials are ever required.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} not found")

    document.status = DocumentStatus.processing
    db.commit()

    graph = _build_graph(
        db,
        textract_client if textract_client is not None else get_textract_client(),
        llm_client if llm_client is not None else get_llm_client(),
    )

    try:
        graph.invoke({"document_id": str(document_id)})
    except Exception:
        document.status = DocumentStatus.failed
        db.commit()
        raise

    db.refresh(document)
    if document.status == DocumentStatus.processing:
        # persist() left status alone (bank-statement passthrough, or an
        # invoice/receipt path that ran to completion) -> done.
        document.status = DocumentStatus.done
        db.commit()
        db.refresh(document)
    return document
