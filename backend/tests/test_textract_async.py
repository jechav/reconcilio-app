"""Issue #18 -- Textract async job APIs for multi-page PDFs.

`AwsTextractClient`'s sync calls (`Document={"Bytes": ...}`) reject any
PDF with more than one page. These tests cover the two pieces that fix
that: `requires_async` (page-count-based routing, no network) and the
async Start.../Get... job-polling machinery itself, faked at the boto3
client boundary the same way `test_reliability.py` fakes the sync calls.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from app.extraction.textract import (
    AwsTextractClient,
    TextractJobFailed,
    TextractJobTimeout,
    requires_async,
)


def _pdf_bytes(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# --- requires_async: routing, no network -----------------------------------


def test_single_page_pdf_does_not_require_async():
    assert requires_async(_pdf_bytes(1)) is False


def test_multi_page_pdf_requires_async():
    assert requires_async(_pdf_bytes(3)) is True


def test_image_bytes_never_require_async():
    assert requires_async(b"\x89PNG\r\n\x1a\nfake png bytes") is False


def test_bytes_that_are_not_a_valid_pdf_fall_back_to_sync():
    # Looks like a PDF (starts with the magic bytes) but pypdf can't parse
    # it -- the safe fallback is the pre-existing sync behaviour, not a
    # forced async route; a genuine problem still surfaces as a Textract
    # error either way.
    assert requires_async(b"%PDF-1.4 not actually a valid pdf structure") is False


# --- AwsTextractClient: async job polling -----------------------------------


class FakeBoto:
    """Records Start.../Get... calls and replays a scripted sequence of
    GetXxx responses, one per poll -- mirrors the FakeBoto pattern in
    test_reliability.py for the sync calls."""

    def __init__(self, get_responses: list[dict], job_id: str = "job-1") -> None:
        self._get_responses = list(get_responses)
        self.job_id = job_id
        self.start_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def start_document_text_detection(self, **kwargs):
        self.start_calls.append(kwargs)
        return {"JobId": self.job_id}

    def get_document_text_detection(self, **kwargs):
        self.get_calls.append(kwargs)
        return self._get_responses.pop(0)

    def start_expense_analysis(self, **kwargs):
        self.start_calls.append(kwargs)
        return {"JobId": self.job_id}

    def get_expense_analysis(self, **kwargs):
        self.get_calls.append(kwargs)
        return self._get_responses.pop(0)

    def start_document_analysis(self, **kwargs):
        self.start_calls.append(kwargs)
        return {"JobId": self.job_id}

    def get_document_analysis(self, **kwargs):
        self.get_calls.append(kwargs)
        return self._get_responses.pop(0)


def _client(fake_boto: FakeBoto, **overrides) -> AwsTextractClient:
    client = AwsTextractClient(region_name="us-east-1", bucket="reconcilio", **overrides)
    client._boto_client = lambda: fake_boto  # type: ignore[method-assign]
    return client


def test_detect_text_async_submits_to_s3_and_returns_lines_once_job_succeeds():
    fake_boto = FakeBoto(
        [
            {"JobStatus": "SUCCEEDED", "Blocks": [{"BlockType": "LINE", "Text": "hello"}]},
        ]
    )
    client = _client(fake_boto, sleep=lambda _s: None)

    lines = client.detect_text_async("org/doc.pdf")

    assert lines == ["hello"]
    assert fake_boto.start_calls == [
        {"DocumentLocation": {"S3Object": {"Bucket": "reconcilio", "Name": "org/doc.pdf"}}}
    ]


def test_detect_text_async_polls_until_the_job_reports_a_terminal_status():
    fake_boto = FakeBoto(
        [
            {"JobStatus": "IN_PROGRESS"},
            {"JobStatus": "IN_PROGRESS"},
            {"JobStatus": "SUCCEEDED", "Blocks": [{"BlockType": "LINE", "Text": "done"}]},
        ]
    )
    sleeps: list[float] = []
    client = _client(fake_boto, poll_interval=1.0, poll_timeout=100.0, sleep=sleeps.append)

    lines = client.detect_text_async("org/doc.pdf")

    assert lines == ["done"]
    assert sleeps == [1.0, 1.0]
    assert len(fake_boto.get_calls) == 3


def test_detect_text_async_paginates_through_every_next_token():
    fake_boto = FakeBoto(
        [
            {
                "JobStatus": "SUCCEEDED",
                "Blocks": [{"BlockType": "LINE", "Text": "page one"}],
                "NextToken": "tok-2",
            },
            {"JobStatus": "SUCCEEDED", "Blocks": [{"BlockType": "LINE", "Text": "page two"}]},
        ]
    )
    client = _client(fake_boto, sleep=lambda _s: None)

    lines = client.detect_text_async("org/doc.pdf")

    assert lines == ["page one", "page two"]
    assert fake_boto.get_calls[1].get("NextToken") == "tok-2"


def test_analyze_expense_async_merges_fields_across_pages():
    fake_boto = FakeBoto(
        [
            {
                "JobStatus": "SUCCEEDED",
                "ExpenseDocuments": [
                    {
                        "SummaryFields": [
                            {
                                "Type": {"Text": "VENDOR_NAME"},
                                "ValueDetection": {"Text": "Acme Co", "Confidence": 95.0},
                            }
                        ]
                    }
                ],
                "NextToken": "tok-2",
            },
            {
                "JobStatus": "SUCCEEDED",
                "ExpenseDocuments": [
                    {
                        "SummaryFields": [
                            {
                                "Type": {"Text": "TOTAL"},
                                "ValueDetection": {"Text": "123.45", "Confidence": 90.0},
                            }
                        ]
                    }
                ],
            },
        ]
    )
    client = _client(fake_boto, sleep=lambda _s: None)

    result = client.analyze_expense_async("org/invoice.pdf")

    names = sorted(f.name for f in result.fields)
    assert names == ["amount", "vendor"]


def test_analyze_document_async_merges_blocks_across_pages_for_table_parsing():
    fake_boto = FakeBoto(
        [
            {"JobStatus": "SUCCEEDED", "Blocks": [{"Id": "a", "BlockType": "TABLE"}], "NextToken": "tok-2"},
            {"JobStatus": "SUCCEEDED", "Blocks": [{"Id": "b", "BlockType": "CELL"}]},
        ]
    )
    client = _client(fake_boto, sleep=lambda _s: None)

    response = client.analyze_document_async("org/statement.pdf")

    assert [b["Id"] for b in response["Blocks"]] == ["a", "b"]


def test_async_job_that_reports_failed_raises_textract_job_failed():
    fake_boto = FakeBoto([{"JobStatus": "FAILED", "StatusMessage": "unsupported document"}])
    client = _client(fake_boto, sleep=lambda _s: None)

    with pytest.raises(TextractJobFailed, match="unsupported document"):
        client.detect_text_async("org/doc.pdf")


def test_async_job_that_never_finishes_raises_textract_job_timeout_instead_of_hanging():
    # Every poll comes back IN_PROGRESS; a small poll_timeout means the
    # client gives up instead of looping forever.
    fake_boto = FakeBoto([{"JobStatus": "IN_PROGRESS"}] * 10)
    client = _client(fake_boto, poll_interval=1.0, poll_timeout=2.0, sleep=lambda _s: None)

    with pytest.raises(TextractJobTimeout):
        client.detect_text_async("org/doc.pdf")


def test_async_call_without_a_configured_bucket_fails_fast():
    client = AwsTextractClient(region_name="us-east-1")

    with pytest.raises(ValueError, match="bucket"):
        client.detect_text_async("org/doc.pdf")
