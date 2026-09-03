"""Issue #7 -- pipeline reliability & observability.

Covers the three required scenarios (AC6): a retried-then-succeeding
network call, a call that exhausts retries and dead-letters, and a re-run
of an already-completed pipeline step producing no duplicate rows -- plus
per-tenant LLM usage tracking (AC5) and PII-free structured logs (AC4).
"""

import json
import logging
import uuid
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.celery_app import process_document
from app.dead_letter import record_dead_letter
from app.extraction.textract import AwsTextractClient
from app.extraction.types import ExtractedField, ExtractedLine
from app.llm_usage import record_llm_call, usage_summary
from app.logging_config import JsonFormatter
from app.models import (
    DeadLetterTask,
    Document,
    DocumentStatus,
    DocumentType,
    ExtractionMethod,
    ExtractionResult,
    LlmUsage,
    Organization,
    Transaction,
)
from app.pipeline import PipelineDeps, run_pipeline
from tests.textract_fixtures import FakeTextractClient, textract_table_response

CSV_STATEMENT = b"""Date,Description,Amount
2026-01-04,COFFEE ROASTERS,-4.50
2026-01-06,CLIENT PAYMENT ACME,1200.00
"""


def _make_document(
    db_session,
    doc_type: DocumentType = DocumentType.bank_statement,
    filename: str = "statement.csv",
) -> Document:
    org = Organization(name="Acme Tax", confidence_threshold=Decimal("0.80"))
    db_session.add(org)
    db_session.flush()
    document = Document(
        org_id=org.id,
        filename=filename,
        content_type="text/csv" if filename.endswith(".csv") else "application/pdf",
        size_bytes=1024,
        minio_key=f"{org.id}/{uuid.uuid4()}-{filename}",
        doc_type=doc_type,
        status=DocumentStatus.queued,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


class FakeRefiner:
    PROVIDER = "openrouter"
    model = "fake-model"

    def refine(self, line, field_names):
        return {}


class NoProviderRefiner:
    """No PROVIDER attribute at all -- mimics a bare test fake that predates
    issue #7; must not blow up usage recording."""

    def refine(self, line, field_names):
        return {}


def _deps(**overrides) -> PipelineDeps:
    base = dict(
        fetch_bytes=lambda key: CSV_STATEMENT,
        textract=FakeTextractClient({"Blocks": []}),
        refiner=FakeRefiner(),
    )
    base.update(overrides)
    return PipelineDeps(**base)


# --- AC3/AC6: idempotency under retry -----------------------------------


def test_rerun_of_completed_document_produces_no_duplicate_rows(db_session):
    document = _make_document(db_session)
    deps = _deps()

    first = run_pipeline(document.id, db_session, deps)
    assert first.status == DocumentStatus.done
    transaction_count = db_session.scalar(
        select(Transaction.id).where(Transaction.document_id == document.id)
    )
    assert transaction_count is not None
    all_transactions = list(db_session.execute(select(Transaction).where(Transaction.document_id == document.id)).scalars())
    all_results = list(
        db_session.execute(select(ExtractionResult).where(ExtractionResult.document_id == document.id)).scalars()
    )
    assert len(all_transactions) == 2
    assert len(all_results) == 2

    # Simulate a redelivered/retried Celery task for the same Document --
    # run_pipeline must short-circuit rather than re-extract.
    second = run_pipeline(document.id, db_session, deps)
    assert second.id == first.id

    all_transactions_after = list(
        db_session.execute(select(Transaction).where(Transaction.document_id == document.id)).scalars()
    )
    all_results_after = list(
        db_session.execute(select(ExtractionResult).where(ExtractionResult.document_id == document.id)).scalars()
    )
    assert len(all_transactions_after) == 2
    assert len(all_results_after) == 2


def test_persist_upserts_when_reentered_mid_flight(db_session):
    """Defense in depth beyond the terminal-status short-circuit: if persist
    is ever re-entered for a Document that already has rows for a line (the
    task committed successfully but was retried anyway), it updates those
    rows in place instead of violating the unique (document_id,
    line_number) constraint."""
    document = _make_document(db_session)
    deps = _deps()

    run_pipeline(document.id, db_session, deps)

    # Force back to "processing" to bypass the terminal-status short-circuit
    # and exercise persist's own upsert behaviour directly.
    document.status = DocumentStatus.processing
    db_session.commit()

    run_pipeline(document.id, db_session, deps)

    all_transactions = list(
        db_session.execute(select(Transaction).where(Transaction.document_id == document.id)).scalars()
    )
    all_results = list(
        db_session.execute(select(ExtractionResult).where(ExtractionResult.document_id == document.id)).scalars()
    )
    assert len(all_transactions) == 2
    assert len(all_results) == 2


# --- AC5: per-tenant LLM usage tracking -----------------------------------


def test_llm_usage_recorded_only_for_real_refiner_calls(db_session):
    org = Organization(name="Acme Tax", confidence_threshold=Decimal("0.80"))
    db_session.add(org)
    db_session.flush()

    record_llm_call(db_session, org_id=org.id, document_id=None, provider="openrouter", model="haiku")
    record_llm_call(db_session, org_id=org.id, document_id=None, provider="openrouter", model="haiku")
    record_llm_call(db_session, org_id=org.id, document_id=None, provider="litellm", model="gpt-4o-mini")
    db_session.commit()

    totals = usage_summary(db_session, org.id)
    by_key = {(t.provider, t.model): t.calls for t in totals}
    assert by_key[("openrouter", "haiku")] == 2
    assert by_key[("litellm", "gpt-4o-mini")] == 1


def test_null_refiner_records_no_usage(db_session):
    document = _make_document(db_session, filename="statement.pdf")
    # Force a low-confidence field so llm_refine actually calls the refiner.
    textract = FakeTextractClient(
        textract_table_response(
            [
                [("Date", 100.0), ("Description", 100.0), ("Amount", 100.0)],
                [("2026-01-04", 40.0), ("COFFEE ROASTERS", 40.0), ("-4.50", 40.0)],
            ]
        )
    )
    deps = _deps(
        fetch_bytes=lambda key: b"fake pdf bytes",
        textract=textract,
        refiner=NoProviderRefiner(),
    )
    run_pipeline(document.id, db_session, deps)

    usage_rows = list(db_session.execute(select(LlmUsage)).scalars())
    assert usage_rows == []


# --- AC1: retried-then-succeeding network call ----------------------------


def test_textract_client_retries_transient_error_then_succeeds(monkeypatch):
    client = AwsTextractClient(region_name="us-east-1")

    calls = {"n": 0}

    class FakeBoto:
        def detect_document_text(self, Document):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
                    "DetectDocumentText",
                )
            return {"Blocks": [{"BlockType": "LINE", "Text": "hello"}]}

    monkeypatch.setattr(client, "_boto_client", lambda: FakeBoto())
    monkeypatch.setattr("app.retry.time.sleep", lambda _seconds: None)

    result = client.detect_text(b"bytes")

    assert result == ["hello"]
    assert calls["n"] == 3


def test_textract_client_does_not_retry_non_transient_error(monkeypatch):
    client = AwsTextractClient(region_name="us-east-1")
    calls = {"n": 0}

    class FakeBoto:
        def detect_document_text(self, Document):
            calls["n"] += 1
            raise ClientError(
                {"Error": {"Code": "InvalidParameterException", "Message": "bad input"}},
                "DetectDocumentText",
            )

    monkeypatch.setattr(client, "_boto_client", lambda: FakeBoto())
    monkeypatch.setattr("app.retry.time.sleep", lambda _seconds: None)

    with pytest.raises(ClientError):
        client.detect_text(b"bytes")

    assert calls["n"] == 1


def test_process_document_task_retries_then_succeeds(db_session, monkeypatch):
    document = _make_document(db_session)

    from app import pipeline as pipeline_module

    calls = {"n": 0}
    real_run_pipeline = pipeline_module.run_pipeline

    def flaky_run_pipeline(document_id, db, deps=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient DB hiccup")
        return real_run_pipeline(document_id, db, deps or _deps())

    monkeypatch.setattr(pipeline_module, "run_pipeline", flaky_run_pipeline)
    monkeypatch.setattr("app.celery_app.RETRY_BACKOFF", 0)

    result = process_document.apply(args=[str(document.id)]).get()

    assert calls["n"] == 2
    assert result in (DocumentStatus.done.value, DocumentStatus.needs_review.value)


# --- AC2: exhausted retries dead-letter -----------------------------------


def test_process_document_task_dead_letters_after_exhausting_retries(db_session, monkeypatch):
    document = _make_document(db_session)

    def always_fails(document_id, db, deps=None):
        raise RuntimeError("permanently broken")

    monkeypatch.setattr("app.pipeline.run_pipeline", always_fails)
    monkeypatch.setattr("app.celery_app.RETRY_BACKOFF", 0)

    result = process_document.apply(args=[str(document.id)]).get()

    assert result == "dead_lettered"

    dead_letters = list(
        db_session.execute(select(DeadLetterTask).where(DeadLetterTask.document_id == document.id)).scalars()
    )
    assert len(dead_letters) == 1
    assert dead_letters[0].task_name == "reconcilio.process_document"
    assert dead_letters[0].attempts >= 1
    assert "permanently broken" in dead_letters[0].error


def test_record_dead_letter_truncates_long_error(db_session):
    document = _make_document(db_session)
    entry = record_dead_letter(
        db_session,
        document_id=document.id,
        org_id=document.org_id,
        task_name="reconcilio.process_document",
        error="x" * 5000,
        attempts=3,
    )
    db_session.commit()
    assert len(entry.error) == 2000


# --- AC4: structured JSON logs, no PII ------------------------------------


def test_json_formatter_produces_valid_json_with_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="reconcilio.pipeline",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="document.processing_finished",
        args=(),
        exc_info=None,
    )
    record.document_id = "abc-123"
    record.status = "done"

    line = formatter.format(record)
    parsed = json.loads(line)

    assert parsed["message"] == "document.processing_finished"
    assert parsed["document_id"] == "abc-123"
    assert parsed["status"] == "done"
    assert parsed["level"] == "INFO"
    assert "logger" in parsed and "timestamp" in parsed


def test_pipeline_logs_carry_no_document_content(db_session, caplog):
    document = _make_document(db_session)
    deps = _deps()

    with caplog.at_level(logging.INFO, logger="reconcilio.pipeline"):
        run_pipeline(document.id, db_session, deps)

    for record in caplog.records:
        text = record.getMessage()
        assert "COFFEE ROASTERS" not in text
        assert "CLIENT PAYMENT" not in text
        assert document.filename not in text
        for value in vars(record).values():
            if isinstance(value, str):
                assert "COFFEE ROASTERS" not in value
                assert document.filename not in value
