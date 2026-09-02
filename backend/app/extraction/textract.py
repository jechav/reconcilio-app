"""AWS Textract client wrapper for invoice/receipt extraction (issue #3).

Two Textract APIs are used, each for a distinct pipeline step so we never
pay for the more expensive structured call when a document turns out not to
be an invoice/receipt:

- `detect_text`: generic OCR (Textract's DetectDocumentText) used by
  `classify_document` to get raw text lines cheaply, to confirm the
  declared Document.doc_type actually looks like what it claims to be.
- `analyze_expense`: Textract's AnalyzeExpense API, purpose-built for
  invoices/receipts -- returns per-field values with a Confidence score
  (0-100) for exactly the fields we care about (vendor, total, date).

Real credentials are never available in CI/tests; `TextractClient` is a
Protocol so tests inject a fake implementing the same two methods, while
`BotoTextractClient` is the real AWS-backed implementation used in
production (constructed lazily -- building a boto3 client does not itself
require valid credentials, only calling it does).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

# Textract AnalyzeExpense SummaryField "Type.Text" values we understand,
# mapped to this app's field names (see ExtractionResult.field_name).
_TEXTRACT_FIELD_MAP: dict[str, str] = {
    "VENDOR_NAME": "vendor",
    "TOTAL": "amount",
    "AMOUNT_DUE": "amount",
    "INVOICE_RECEIPT_DATE": "invoice_date",
}

FIELD_NAMES = ("vendor", "amount", "invoice_date")


@dataclass(frozen=True)
class ExtractedField:
    name: str
    value: str
    confidence: float  # normalized 0.0-1.0


@dataclass(frozen=True)
class TextractExpenseResult:
    fields: list[ExtractedField]


class TextractClient(Protocol):
    def detect_text(self, document_bytes: bytes) -> list[str]:
        """Return raw text lines, cheapest-possible OCR pass."""
        ...

    def analyze_expense(self, document_bytes: bytes) -> TextractExpenseResult:
        """Structured invoice/receipt extraction with per-field confidence."""
        ...


class BotoTextractClient:
    """Real AWS Textract-backed implementation."""

    def __init__(self, region_name: str | None = None) -> None:
        self._region_name = region_name
        self._client = None

    def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            import boto3

            self._client = boto3.client("textract", region_name=self._region_name)
        return self._client

    def detect_text(self, document_bytes: bytes) -> list[str]:
        response = self._get_client().detect_document_text(Document={"Bytes": document_bytes})
        return [
            block["Text"]
            for block in response.get("Blocks", [])
            if block.get("BlockType") == "LINE" and "Text" in block
        ]

    def analyze_expense(self, document_bytes: bytes) -> TextractExpenseResult:
        response = self._get_client().analyze_expense(Document={"Bytes": document_bytes})
        fields: list[ExtractedField] = []
        for expense_doc in response.get("ExpenseDocuments", []):
            for summary_field in expense_doc.get("SummaryFields", []):
                field_type = summary_field.get("Type", {}).get("Text")
                mapped_name = _TEXTRACT_FIELD_MAP.get(field_type)
                if mapped_name is None:
                    continue
                value_detection = summary_field.get("ValueDetection", {})
                value = value_detection.get("Text")
                confidence = value_detection.get("Confidence")
                if value is None or confidence is None:
                    continue
                fields.append(
                    ExtractedField(name=mapped_name, value=value, confidence=confidence / 100.0)
                )
        return TextractExpenseResult(fields=fields)


@lru_cache
def get_textract_client() -> TextractClient:
    return BotoTextractClient()
