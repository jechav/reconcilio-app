"""The extraction pipeline.

Graph shape (unchanged from the issue #2 skeleton, now with real bodies for
the bank-statement path):

    classify -+- ocr_extract -> llm_refine -+- validate -> categorize -> persist
              +- structured_parse ----------+

`classify` picks the branch from the Document's format. A PDF/image bank
statement takes the Textract + LLM-refinement branch; a CSV or OFX statement
is already machine-readable, so it skips OCR and refinement entirely and
takes the dedicated structured-parse branch instead. Both branches converge
on the *same* validate/persist steps, which is what keeps the audit trail
uniform: every line, on every path, becomes one `ExtractionResult` recording
per-field value, confidence and method.

Invoice/receipt extraction is issue #3's branch and is still a pass-through
here.

The whole thing is callable as a plain function -- `run_pipeline(doc_id, db,
deps)` -- with object storage, the Textract client and the LLM refiner
injected, so tests drive real pipeline behaviour with fakes only at those
three network boundaries.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.extraction import structured
from app.extraction import textract as textract_module
from app.extraction.llm import LlmRefiner, NullRefiner, OpenRouterRefiner
from app.extraction.textract import TextractClient
from app.extraction.types import ExtractedLine
from app.extraction.validation import ValidationOutcome, validate_lines
from app.models import (
    SYSTEM_ACTOR,
    AuditLogEntry,
    Document,
    DocumentStatus,
    DocumentType,
    ExtractionResult,
    Organization,
    Transaction,
    TransactionStatus,
)

OCR_PATH = "ocr"
STRUCTURED_PATH = "structured"
#: Invoice/receipt extraction lands in issue #3; until then it is a no-op.
PASSTHROUGH_PATH = "passthrough"


class ExtractionFailed(RuntimeError):
    """Nothing usable could be extracted -- the Document is marked failed."""


@dataclass
class PipelineDeps:
    """The pipeline's three network boundaries, injectable for tests."""

    fetch_bytes: Callable[[str], bytes]
    textract: TextractClient
    refiner: LlmRefiner


def default_deps() -> PipelineDeps:
    from app.config import get_settings
    from app.storage import get_object_bytes

    settings = get_settings()
    refiner: LlmRefiner = (
        OpenRouterRefiner(
            api_key=settings.openrouter_api_key,
            model=settings.llm_model,
            base_url=settings.openrouter_base_url,
        )
        if settings.openrouter_api_key
        else NullRefiner()
    )
    return PipelineDeps(
        fetch_bytes=get_object_bytes,
        textract=textract_module.AwsTextractClient(region_name=settings.aws_region),
        refiner=refiner,
    )


class PipelineState(TypedDict, total=False):
    document_id: str
    path: str
    lines: list[ExtractedLine]
    outcomes: list[ValidationOutcome]
    refined_lines: list[int]


def _build_graph(document: Document, db: Session, deps: PipelineDeps, threshold: float) -> Any:
    """Compile the graph with every node bound to one Document and its deps."""

    def classify(state: PipelineState) -> PipelineState:
        if document.doc_type != DocumentType.bank_statement:
            return {**state, "path": PASSTHROUGH_PATH}
        if structured.is_structured(document.filename):
            return {**state, "path": STRUCTURED_PATH}
        return {**state, "path": OCR_PATH}

    def structured_parse(state: PipelineState) -> PipelineState:
        data = deps.fetch_bytes(document.minio_key)
        return {**state, "lines": structured.parse_structured(document.filename, data)}

    def ocr_extract(state: PipelineState) -> PipelineState:
        data = deps.fetch_bytes(document.minio_key)
        response = deps.textract.analyze_document(data)
        lines = textract_module.parse_textract_tables(response)
        if not lines:
            raise ExtractionFailed("Textract found no statement table in the document")
        return {**state, "lines": lines}

    def llm_refine(state: PipelineState) -> PipelineState:
        refined_lines: list[int] = []
        for line in state.get("lines", []):
            weak = line.low_confidence_fields(threshold)
            if not weak:
                continue
            replacements = deps.refiner.refine(line, weak)
            if not replacements:
                continue
            line.fields.update(replacements)
            refined_lines.append(line.line_number)
        return {**state, "refined_lines": refined_lines}

    def validate(state: PipelineState) -> PipelineState:
        lines = state.get("lines", [])
        outcomes = validate_lines(lines)
        if lines and not any(outcome.ok for outcome in outcomes):
            raise ExtractionFailed(
                "No statement line passed schema validation: "
                + "; ".join(o.error or "" for o in outcomes[:3])
            )
        return {**state, "outcomes": outcomes}

    def categorize(state: PipelineState) -> PipelineState:
        # Category assignment is a later ticket; every Transaction starts
        # uncategorized and the review UI is where a human picks one.
        return state

    def persist(state: PipelineState) -> PipelineState:
        _persist_lines(
            document=document,
            db=db,
            lines=state.get("lines", []),
            outcomes=state.get("outcomes", []),
            refined_lines=state.get("refined_lines", []),
            threshold=threshold,
            path=state.get("path", PASSTHROUGH_PATH),
        )
        return state

    graph = StateGraph(PipelineState)
    graph.add_node("classify", classify)
    graph.add_node("ocr_extract", ocr_extract)
    graph.add_node("llm_refine", llm_refine)
    graph.add_node("structured_parse", structured_parse)
    graph.add_node("validate", validate)
    graph.add_node("categorize", categorize)
    graph.add_node("persist", persist)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        lambda state: state.get("path", PASSTHROUGH_PATH),
        {
            OCR_PATH: "ocr_extract",
            STRUCTURED_PATH: "structured_parse",
            PASSTHROUGH_PATH: "validate",
        },
    )
    graph.add_edge("ocr_extract", "llm_refine")
    graph.add_edge("llm_refine", "validate")
    graph.add_edge("structured_parse", "validate")
    graph.add_edge("validate", "categorize")
    graph.add_edge("categorize", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


def _line_status(line: ExtractedLine, threshold: float) -> TransactionStatus:
    """A line needs review when any field is still below the threshold.

    Structured-parse lines are all confidence 1.0 and so are always
    resolved; OCR/LLM lines are only resolved once refinement has brought
    every field up to the Organization's bar.
    """
    if line.low_confidence_fields(threshold):
        return TransactionStatus.needs_review
    return TransactionStatus.resolved


def _persist_lines(
    *,
    document: Document,
    db: Session,
    lines: list[ExtractedLine],
    outcomes: list[ValidationOutcome],
    refined_lines: list[int],
    threshold: float,
    path: str,
) -> None:
    by_line_number = {line.line_number: line for line in lines}
    created: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for outcome in outcomes:
        raw = by_line_number.get(outcome.line_number)
        if raw is None:
            continue
        if outcome.line is None:
            # Kept out of the database but never silently dropped: the
            # rejection and its reason go into the Document's audit entry.
            rejected.append({"line_number": outcome.line_number, "error": outcome.error})
            continue

        transaction = Transaction(
            org_id=document.org_id,
            document_id=document.id,
            line_number=outcome.line.line_number,
            txn_date=outcome.line.txn_date,
            description=outcome.line.description,
            amount=outcome.line.amount,
            confidence=raw.min_confidence,
            status=_line_status(raw, threshold),
        )
        db.add(transaction)
        db.flush()

        db.add(
            ExtractionResult(
                org_id=document.org_id,
                document_id=document.id,
                transaction_id=transaction.id,
                line_number=raw.line_number,
                method=raw.weakest_method,
                confidence=raw.min_confidence,
                fields=raw.to_json(),
            )
        )
        db.add(
            AuditLogEntry(
                org_id=document.org_id,
                actor=SYSTEM_ACTOR,
                action="transaction.extracted",
                entity_type="transaction",
                entity_id=transaction.id,
                before=None,
                after={
                    "document_id": str(document.id),
                    "line_number": transaction.line_number,
                    "txn_date": transaction.txn_date.isoformat(),
                    "description": transaction.description,
                    "amount": str(transaction.amount),
                    "confidence": transaction.confidence,
                    "status": transaction.status.value,
                    "fields": raw.to_json(),
                },
            )
        )
        created.append({"transaction_id": str(transaction.id), "line_number": transaction.line_number})

    db.add(
        AuditLogEntry(
            org_id=document.org_id,
            actor=SYSTEM_ACTOR,
            action="document.extracted",
            entity_type="document",
            entity_id=document.id,
            before={"status": DocumentStatus.processing.value},
            after={
                "path": path,
                "confidence_threshold": threshold,
                "lines_found": len(lines),
                "transactions_created": len(created),
                "refined_lines": refined_lines,
                "rejected_lines": rejected,
            },
        )
    )
    db.flush()


def derive_document_status(transactions: list[Transaction]) -> DocumentStatus:
    """A Document's status is an aggregate of its Transactions' statuses.

    `done` only once every Transaction is resolved; a single Transaction
    needing review pulls the whole Document to `needs_review`. A Document
    with no Transactions (the invoice/receipt pass-through) has nothing
    outstanding, so it is done.
    """
    if any(t.status == TransactionStatus.needs_review for t in transactions):
        return DocumentStatus.needs_review
    return DocumentStatus.done


def run_pipeline(document_id: uuid.UUID, db: Session, deps: PipelineDeps | None = None) -> Document:
    """Drive one Document through extraction and return it, status updated.

    Callable directly (no HTTP, no Celery) so it can be exercised as a
    pipeline-as-a-function test, and reused by the Celery task.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} not found")

    document.status = DocumentStatus.processing
    db.commit()

    organization = db.get(Organization, document.org_id)
    threshold = organization.confidence_threshold if organization is not None else 0.8

    if deps is None:
        deps = default_deps()

    try:
        _build_graph(document, db, deps, threshold).invoke({"document_id": str(document_id)})
    except Exception:
        db.rollback()
        failed = db.get(Document, document_id)
        if failed is not None:
            failed.status = DocumentStatus.failed
            db.commit()
        raise

    document.status = derive_document_status(list(document.transactions))
    db.commit()
    db.refresh(document)
    return document
