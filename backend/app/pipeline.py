"""The extraction pipeline.

Graph shape:

    classify -+- invoice_ocr_extract -> invoice_llm_refine -+
              +- ocr_extract -> llm_refine -------------------+- validate -> categorize -> persist
              +- structured_parse -----------------------------+
              +- flag_unknown -> END

`classify` picks the branch from the Document's declared `doc_type` (and,
for invoice/receipt, cross-checks it against a cheap OCR pass -- issue #3,
AC1). A PDF/image bank statement takes the Textract-TABLES + line-level
LLM-refinement branch; a CSV or OFX statement is already machine-readable,
so it skips OCR and refinement entirely and takes the dedicated
structured-parse branch (issue #4). An invoice/receipt takes Textract's
AnalyzeExpense + per-field vision-LLM-refinement branch (issue #3). All
three converge on the *same* validate/persist steps, which is what keeps
the audit trail uniform: every line, on every path, becomes one
`ExtractionResult` recording per-field value, confidence and method, and
one `Transaction` (an invoice/receipt Document always has exactly one,
`line_number` 1; a bank statement has one per statement line).

The whole thing is callable as a plain function -- `run_pipeline(doc_id, db,
deps)` -- with object storage, the Textract client and the LLM refiner(s)
injected, so tests drive real pipeline behaviour with fakes only at those
network boundaries.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction import structured
from app.extraction import textract as textract_module
from app.extraction.categorize import (
    CategoryClassifier,
    CorrectionExample,
    NullClassifier,
    OpenRouterCategoryClassifier,
)
from app.extraction.classify import detect_document_type
from app.extraction.embed import EmbeddingClient, LiteLLMEmbeddingClient, NullEmbeddingClient
from app.extraction.llm import LlmRefiner, LLMRefinementClient, NullRefiner, OpenRouterRefiner, get_llm_client
from app.extraction.textract import FIELD_NAMES, TextractClient
from app.extraction.types import ExtractedField, ExtractedLine
from app.extraction.validation import ValidationOutcome, validate_line, validate_lines
from app.llm_usage import record_llm_call
from app.models import (
    SYSTEM_ACTOR,
    AuditLogEntry,
    Category,
    CategoryCorrection,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    EmbeddingSourceType,
    ExtractionMethod,
    ExtractionResult,
    Organization,
    Transaction,
    TransactionStatus,
)
from app.reconciliation import run_reconciliation_for_document
from app.scoping import org_scoped_select

logger = logging.getLogger("reconcilio.pipeline")

#: A Document in one of these statuses already ran the pipeline to
#: completion -- issue #7 AC3/AC6: a retried/redelivered `process_document`
#: task for the same Document must not re-run extraction and risk duplicate
#: Transaction/ExtractionResult rows. `failed` is deliberately excluded: a
#: prior failed attempt is exactly what a retry exists to redo.
_TERMINAL_STATUSES = frozenset({DocumentStatus.done, DocumentStatus.needs_review})

OCR_PATH = "ocr"
STRUCTURED_PATH = "structured"
INVOICE_PATH = "invoice"
#: classify_document could not confidently place the Document (issue #3, AC1).
UNKNOWN_PATH = "unknown"


class ExtractionFailed(RuntimeError):
    """Nothing usable could be extracted -- the Document is marked failed."""


@dataclass
class PipelineDeps:
    """The pipeline's network boundaries, injectable for tests.

    `refiner` is the text-based, line-level refiner the bank-statement path
    uses; `llm_client` is the vision-based, per-field refiner the
    invoice/receipt path uses (see app/extraction/llm.py for why these are
    two different Protocols). `llm_client` is optional -- tests that only
    exercise the bank-statement paths never need to set it, and the
    invoice/receipt path degrades to "no refinement" (like `NullRefiner`)
    when it's absent. `classifier` suggests each persisted Transaction's
    Category (issue #5); defaults to `NullClassifier` for tests that don't
    set it explicitly.
    """

    fetch_bytes: Callable[[str], bytes]
    textract: TextractClient
    refiner: LlmRefiner
    llm_client: LLMRefinementClient | None = None
    classifier: CategoryClassifier = field(default_factory=NullClassifier)
    #: Generates Document/Transaction embeddings on persist for the RAG chat
    #: agent's vector-search tool (issue #11). Defaults to NullEmbeddingClient
    #: for tests that don't set it explicitly -- see extraction/embed.py.
    embedding_client: EmbeddingClient = field(default_factory=NullEmbeddingClient)


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
    classifier: CategoryClassifier = (
        OpenRouterCategoryClassifier(
            api_key=settings.openrouter_api_key,
            model=settings.llm_model,
            base_url=settings.openrouter_base_url,
        )
        if settings.openrouter_api_key
        else NullClassifier()
    )
    embedding_client: EmbeddingClient = (
        LiteLLMEmbeddingClient(model=settings.embedding_model)
        if settings.openrouter_api_key
        else NullEmbeddingClient()
    )
    return PipelineDeps(
        fetch_bytes=get_object_bytes,
        textract=textract_module.AwsTextractClient(region_name=settings.aws_region),
        refiner=refiner,
        llm_client=get_llm_client(),
        classifier=classifier,
        embedding_client=embedding_client,
    )


class PipelineState(TypedDict, total=False):
    document_id: str
    path: str
    document_bytes: bytes
    lines: list[ExtractedLine]
    outcomes: list[ValidationOutcome]
    refined_lines: list[int]


def _build_graph(
    document: Document,
    db: Session,
    deps: PipelineDeps,
    threshold: float,
    categories: list[Category],
    examples: list[CorrectionExample],
) -> Any:
    """Compile the graph with every node bound to one Document and its deps."""

    def classify(state: PipelineState) -> PipelineState:
        if document.doc_type == DocumentType.invoice_or_receipt:
            data = deps.fetch_bytes(document.minio_key)
            raw_lines = deps.textract.detect_text(data)
            detected = detect_document_type(raw_lines)
            if detected != DocumentType.invoice_or_receipt:
                return {**state, "path": UNKNOWN_PATH}
            return {**state, "path": INVOICE_PATH, "document_bytes": data}

        if structured.is_structured(document.filename):
            return {**state, "path": STRUCTURED_PATH}
        return {**state, "path": OCR_PATH}

    def flag_unknown(state: PipelineState) -> PipelineState:
        document.status = DocumentStatus.needs_review
        db.add(
            AuditLogEntry(
                org_id=document.org_id,
                actor=SYSTEM_ACTOR,
                action="document.classification_unknown",
                entity_type="document",
                entity_id=document.id,
                before={"doc_type": document.doc_type.value},
                after={"detected_type": None},
            )
        )
        db.commit()
        return state

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
            _record_refiner_usage(db, document, deps.refiner)
            if not replacements:
                continue
            line.fields.update(replacements)
            refined_lines.append(line.line_number)
        return {**state, "refined_lines": refined_lines}

    def invoice_ocr_extract(state: PipelineState) -> PipelineState:
        document_bytes = state["document_bytes"]
        result = deps.textract.analyze_expense(document_bytes)
        found = {field.name: field for field in result.fields}
        fields: dict[str, ExtractedField] = {}
        for name in FIELD_NAMES:
            match = found.get(name)
            if match is not None:
                fields[name] = ExtractedField(value=match.value, confidence=match.confidence, method=ExtractionMethod.ocr)
            else:
                # Textract found nothing for this field -- zero confidence,
                # not "confidently absent", so refinement still targets it.
                fields[name] = ExtractedField(value=None, confidence=0.0, method=ExtractionMethod.ocr)
        return {**state, "lines": [ExtractedLine(line_number=1, fields=fields)]}

    def invoice_llm_refine(state: PipelineState) -> PipelineState:
        lines = state.get("lines", [])
        if not lines:
            return state
        line = lines[0]
        weak = line.low_confidence_fields(threshold)
        refined_lines: list[int] = []
        if weak and deps.llm_client is not None:
            document_bytes = state["document_bytes"]
            for field_name in weak:
                refined = deps.llm_client.refine_field(
                    field_name, document_bytes, document.content_type, line.value(field_name)
                )
                _record_refiner_usage(db, document, deps.llm_client)
                line.fields[field_name] = ExtractedField(
                    value=refined.value, confidence=refined.confidence, method=ExtractionMethod.llm
                )
            refined_lines.append(line.line_number)
        return {**state, "refined_lines": refined_lines}

    def invoice_validate(state: PipelineState) -> PipelineState:
        lines = state.get("lines", [])
        outcomes = [
            validate_line(line, date_field="invoice_date", description_field="vendor", amount_field="amount")
            for line in lines
        ]
        if lines and not any(outcome.ok for outcome in outcomes):
            raise ExtractionFailed(
                "Invoice/receipt failed schema validation: "
                + "; ".join(o.error or "" for o in outcomes[:3])
            )
        return {**state, "outcomes": outcomes}

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
        # Suggestion happens in `persist`, once each line has become a
        # Transaction with an id to attach the suggestion (and its audit
        # entry) to -- this node is a pass-through kept for graph shape
        # symmetry with the other paths (issue #5).
        return state

    def persist(state: PipelineState) -> PipelineState:
        _persist_lines(
            document=document,
            db=db,
            lines=state.get("lines", []),
            outcomes=state.get("outcomes", []),
            refined_lines=state.get("refined_lines", []),
            threshold=threshold,
            path=state.get("path", OCR_PATH),
            classifier=deps.classifier,
            categories=categories,
            examples=examples,
            embedding_client=deps.embedding_client,
        )
        return state

    graph = StateGraph(PipelineState)
    graph.add_node("classify", classify)
    graph.add_node("flag_unknown", flag_unknown)
    graph.add_node("ocr_extract", ocr_extract)
    graph.add_node("llm_refine", llm_refine)
    graph.add_node("invoice_ocr_extract", invoice_ocr_extract)
    graph.add_node("invoice_llm_refine", invoice_llm_refine)
    graph.add_node("invoice_validate", invoice_validate)
    graph.add_node("structured_parse", structured_parse)
    graph.add_node("validate", validate)
    graph.add_node("categorize", categorize)
    graph.add_node("persist", persist)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        lambda state: state.get("path", UNKNOWN_PATH),
        {
            OCR_PATH: "ocr_extract",
            STRUCTURED_PATH: "structured_parse",
            INVOICE_PATH: "invoice_ocr_extract",
            UNKNOWN_PATH: "flag_unknown",
        },
    )
    graph.add_edge("flag_unknown", END)
    graph.add_edge("ocr_extract", "llm_refine")
    graph.add_edge("llm_refine", "validate")
    graph.add_edge("structured_parse", "validate")
    graph.add_edge("invoice_ocr_extract", "invoice_llm_refine")
    graph.add_edge("invoice_llm_refine", "invoice_validate")
    graph.add_edge("invoice_validate", "categorize")
    graph.add_edge("validate", "categorize")
    graph.add_edge("categorize", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


def _record_refiner_usage(db: Session, document: Document, refiner: object) -> None:
    """Record one LlmUsage row for a real refiner call (issue #7, AC5).

    `NullRefiner`/`llm_client is None` never reach this -- only a refiner
    with a `PROVIDER` set (OpenRouterRefiner, LiteLLMRefinementClient, or a
    test fake opting in) made an actual, billable call.
    """
    provider = getattr(refiner, "PROVIDER", None)
    if provider is None:
        return
    model = getattr(refiner, "model", "unknown")
    record_llm_call(db, org_id=document.org_id, document_id=document.id, provider=provider, model=model)


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
    classifier: CategoryClassifier,
    categories: list[Category],
    examples: list[CorrectionExample],
    embedding_client: EmbeddingClient,
) -> None:
    by_line_number = {line.line_number: line for line in lines}
    created: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    persisted_transactions: list[Transaction] = []
    category_names = [category.name for category in categories]
    category_id_by_name = {category.name: category.id for category in categories}

    # Existing rows for this Document, keyed by line_number -- defense in
    # depth for idempotency (issue #7, AC3/AC6) beyond the terminal-status
    # short-circuit in run_pipeline: if persist is ever re-entered for a
    # Document that already has rows for a line (e.g. a task retried after
    # its commit succeeded but before the retry was cancelled), this updates
    # those rows in place instead of violating the unique
    # (document_id, line_number) constraint with a duplicate insert.
    existing_transactions = {
        t.line_number: t
        for t in db.execute(
            select(Transaction).where(Transaction.document_id == document.id)
        ).scalars()
    }
    existing_results = {
        r.line_number: r
        for r in db.execute(
            select(ExtractionResult).where(ExtractionResult.document_id == document.id)
        ).scalars()
    }

    for outcome in outcomes:
        raw = by_line_number.get(outcome.line_number)
        if raw is None:
            continue
        if outcome.line is None:
            # Kept out of the database but never silently dropped: the
            # rejection and its reason go into the Document's audit entry.
            rejected.append({"line_number": outcome.line_number, "error": outcome.error})
            continue

        transaction = existing_transactions.get(outcome.line_number)
        if transaction is None:
            transaction = Transaction(org_id=document.org_id, document_id=document.id, line_number=outcome.line.line_number)
            db.add(transaction)
        transaction.txn_date = outcome.line.txn_date
        transaction.description = outcome.line.description
        transaction.amount = outcome.line.amount
        transaction.confidence = raw.min_confidence
        transaction.status = _line_status(raw, threshold)
        db.flush()

        extraction_result = existing_results.get(outcome.line_number)
        if extraction_result is None:
            extraction_result = ExtractionResult(
                org_id=document.org_id, document_id=document.id, line_number=raw.line_number
            )
            db.add(extraction_result)
        extraction_result.transaction_id = transaction.id
        extraction_result.method = raw.weakest_method
        extraction_result.confidence = raw.min_confidence
        extraction_result.fields = raw.to_json()
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

        # Category suggestion (issue #5, AC3): exactly one suggested
        # Category with a confidence score per Transaction, informed by
        # this Organization's own past corrections only (AC5/AC6).
        suggestion = classifier.suggest(
            description=transaction.description,
            amount=str(transaction.amount),
            category_names=category_names,
            examples=examples,
        )
        suggested_category_id = category_id_by_name.get(suggestion.category_name)
        transaction.category_id = suggested_category_id
        transaction.category_confidence = suggestion.confidence
        db.add(
            AuditLogEntry(
                org_id=document.org_id,
                actor=SYSTEM_ACTOR,
                action="transaction.category_suggested",
                entity_type="transaction",
                entity_id=transaction.id,
                before=None,
                after={
                    "category_id": str(suggested_category_id) if suggested_category_id else None,
                    "category_name": suggestion.category_name,
                    "confidence": suggestion.confidence,
                },
            )
        )

        created.append({"transaction_id": str(transaction.id), "line_number": transaction.line_number})
        persisted_transactions.append(transaction)

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

    _persist_embeddings(
        db=db, document=document, transactions=persisted_transactions, embedding_client=embedding_client
    )
    db.flush()


def _upsert_embedding(
    db: Session,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    transaction_id: uuid.UUID | None,
    source_type: EmbeddingSourceType,
    source_id: uuid.UUID,
    content: str,
    embedding_client: EmbeddingClient,
) -> None:
    """Create or refresh one Embedding row (issue #11, AC1).

    Idempotent by `(source_type, source_id)` -- a document/transaction whose
    text hasn't changed since a prior run still gets its vector recomputed
    (cheap, and correct if the embedding model itself ever changes) rather
    than accumulating duplicate rows, matching the persist step's existing
    update-in-place convention for Transaction/ExtractionResult (see
    `_persist_lines` above, issue #7 AC3/AC6).
    """
    existing = db.execute(
        select(Embedding).where(
            Embedding.source_type == source_type, Embedding.source_id == source_id
        )
    ).scalar_one_or_none()
    vector = embedding_client.embed(content)
    if existing is None:
        db.add(
            Embedding(
                org_id=org_id,
                document_id=document_id,
                transaction_id=transaction_id,
                source_type=source_type,
                source_id=source_id,
                content=content,
                vector=vector,
            )
        )
    else:
        existing.content = content
        existing.vector = vector


def _persist_embeddings(
    *,
    db: Session,
    document: Document,
    transactions: list[Transaction],
    embedding_client: EmbeddingClient,
) -> None:
    """Write embeddings for this Document's text and each of its persisted
    Transactions (issue #11, AC1), so the chat agent's vector-search tool
    (app/chat/tools.py) has something to search. No-ops write-wise when
    `embedding_client` is a NullEmbeddingClient (no LLM configured): the rows
    are still created/refreshed, just with `vector` left None -- see
    extraction/embed.py.
    """
    for transaction in transactions:
        content = f"{transaction.txn_date} {transaction.description} amount={transaction.amount}"
        _upsert_embedding(
            db,
            org_id=document.org_id,
            document_id=document.id,
            transaction_id=transaction.id,
            source_type=EmbeddingSourceType.transaction,
            source_id=transaction.id,
            content=content,
            embedding_client=embedding_client,
        )

    if not transactions:
        return

    document_content = "\n".join(
        [document.filename] + [t.description for t in transactions]
    )
    _upsert_embedding(
        db,
        org_id=document.org_id,
        document_id=document.id,
        transaction_id=None,
        source_type=EmbeddingSourceType.document,
        source_id=document.id,
        content=document_content,
        embedding_client=embedding_client,
    )


def derive_document_status(transactions: list[Transaction]) -> DocumentStatus:
    """A Document's status is an aggregate of its Transactions' statuses.

    `done` only once every Transaction is resolved; a single Transaction
    needing review pulls the whole Document to `needs_review`. A Document
    with no Transactions (a failed/rejected extraction with nothing
    persisted) has nothing outstanding, so it is done.
    """
    if any(t.status == TransactionStatus.needs_review for t in transactions):
        return DocumentStatus.needs_review
    return DocumentStatus.done


def _recent_corrections(db: Session, org_id: uuid.UUID) -> list[CorrectionExample]:
    """The Organization's most recent user corrections, as few-shot context
    for the next Category suggestion -- org-scoped only (issue #5, AC5)."""
    from app.extraction.categorize import FEW_SHOT_EXAMPLE_LIMIT

    stmt = (
        select(CategoryCorrection.description, Category.name)
        .join(Category, Category.id == CategoryCorrection.category_id)
        .where(CategoryCorrection.org_id == org_id)
        .order_by(CategoryCorrection.created_at.desc())
        .limit(FEW_SHOT_EXAMPLE_LIMIT)
    )
    return [
        CorrectionExample(description=description, category_name=category_name)
        for description, category_name in db.execute(stmt).all()
    ]


def run_pipeline(document_id: uuid.UUID, db: Session, deps: PipelineDeps | None = None) -> Document:
    """Drive one Document through extraction and return it, status updated.

    Callable directly (no HTTP, no Celery) so it can be exercised as a
    pipeline-as-a-function test, and reused by the Celery task.
    `deps` defaults to the real AWS/OpenRouter/LiteLLM-backed clients; tests
    inject fakes so no live credentials are ever required.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} not found")

    if document.status in _TERMINAL_STATUSES:
        # Idempotent no-op (issue #7, AC3/AC6): a redelivered/retried task
        # for a Document that already finished a prior run must not
        # re-extract and re-persist -- that would duplicate Transaction and
        # ExtractionResult rows despite the unique (document_id, line_number)
        # constraint being the last-resort guard, not the intended path.
        logger.info(
            "document.already_processed",
            extra={"document_id": str(document_id), "status": document.status.value},
        )
        return document

    logger.info(
        "document.processing_started",
        extra={"document_id": str(document_id), "org_id": str(document.org_id)},
    )

    document.status = DocumentStatus.processing
    db.commit()

    organization = db.get(Organization, document.org_id)
    threshold = float(organization.confidence_threshold) if organization is not None else 0.8

    if deps is None:
        deps = default_deps()

    categories = list(
        db.execute(org_scoped_select(Category, document.org_id).order_by(Category.name)).scalars()
    )
    examples = _recent_corrections(db, document.org_id)

    try:
        _build_graph(document, db, deps, threshold, categories, examples).invoke(
            {"document_id": str(document_id)}
        )
    except Exception as exc:
        db.rollback()
        failed = db.get(Document, document_id)
        if failed is not None:
            failed.status = DocumentStatus.failed
            db.commit()
        logger.warning(
            "document.processing_failed",
            extra={"document_id": str(document_id), "error_type": type(exc).__name__},
        )
        raise

    if document.status != DocumentStatus.needs_review:
        # Anything other than flag_unknown (which already set and committed
        # needs_review itself) derives status from what actually persisted.
        document.status = derive_document_status(list(document.transactions))
        db.commit()

    logger.info(
        "document.processing_finished",
        extra={
            "document_id": str(document_id),
            "org_id": str(document.org_id),
            "status": document.status.value,
            "transaction_count": len(document.transactions),
        },
    )

    # Reconciliation runs incrementally whenever a Document finishes
    # extraction (issue #6, AC1) -- a no-op when nothing was persisted
    # (flag_unknown / fully-rejected extraction).
    run_reconciliation_for_document(document_id, db)

    db.refresh(document)
    return document
