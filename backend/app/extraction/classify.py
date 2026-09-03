"""Content-based document classification (issue #3, AC1).

`Document.doc_type` is a user-declared value at upload time (see
DocumentUploadRequest). This module cross-checks that declaration against
the document's actual content so a mislabeled or unrecognizable upload is
flagged rather than silently run through the wrong extraction path.

Structured formats (CSV/OFX) are trusted as declared -- the file format
itself is a strong, unambiguous signal and there is no OCR step for them.
PDFs/images are checked against a small keyword-marker heuristic over
Textract's raw OCR text lines. Returns `None` for "unknown" (AC1).
"""

from __future__ import annotations

from app.models import DocumentType

STRUCTURED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.intu.qfx",
    "application/x-ofx",
}

_INVOICE_MARKERS = (
    "invoice",
    "receipt",
    "total",
    "amount due",
    "bill to",
    "subtotal",
    "vendor",
)

_BANK_STATEMENT_MARKERS = (
    "statement period",
    "account number",
    "beginning balance",
    "ending balance",
    "account summary",
    "statement of account",
)


def detect_document_type(raw_lines: list[str]) -> DocumentType | None:
    """Classify from OCR'd text lines. None means unknown/unrecognized."""
    text = " ".join(raw_lines).lower()
    invoice_score = sum(1 for marker in _INVOICE_MARKERS if marker in text)
    bank_score = sum(1 for marker in _BANK_STATEMENT_MARKERS if marker in text)

    if invoice_score == 0 and bank_score == 0:
        return None
    if invoice_score >= bank_score:
        return DocumentType.invoice_or_receipt
    return DocumentType.bank_statement
