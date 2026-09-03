"""AWS Textract client: OCR for both extraction paths.

Three Textract calls are used, each for a distinct pipeline step so we
never pay for a more expensive structured call when it isn't needed:

- `detect_text`: generic OCR (Textract's DetectDocumentText) used by
  `classify` on the invoice/receipt path to get raw text lines cheaply, to
  confirm the declared Document.doc_type actually looks like what it
  claims to be (issue #3, AC1).
- `analyze_expense`: Textract's AnalyzeExpense API, purpose-built for
  invoices/receipts -- returns per-field values with a Confidence score
  (0-100) for exactly the fields we care about (vendor, total, date).
- `analyze_document` with the TABLES feature: the right call for a bank
  statement, which is a table -- it gives us cells with per-cell
  confidence, exactly the granularity the audit trail wants (one
  confidence per field, not one per document). `parse_textract_tables`
  below turns that raw response into candidate statement lines.

Real credentials are never available in CI/tests; `TextractClient` is a
Protocol so tests inject a fake implementing these methods, while
`AwsTextractClient` is the real AWS-backed implementation used in
production (constructed lazily -- building a boto3 client does not itself
require valid credentials, only calling it does).

Async job APIs (issue #18): the three sync calls above (`Document={"Bytes":
...}`) reject any PDF with more than one page -- AWS only accepts a
single-page PDF or an image synchronously. Every sync method above has an
`*_async` counterpart (`detect_text_async`, `analyze_expense_async`,
`analyze_document_async`) that instead reads from an S3 object reference
(the Document's existing `minio_key` -- see app/storage.py, this repo
already stores every uploaded Document there) via Textract's Start.../
Get... job APIs, polling for completion rather than using an SNS listener
(this pipeline runs inside a synchronous Celery task, so a blocking poll
loop fits its execution model). `requires_async` is the single place that
decides which path a given document needs, based on its page count -- a
genuinely single-page PDF or an image still takes the cheaper, faster sync
path.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Protocol

from app.extraction.types import ExtractedField as LineField
from app.extraction.types import ExtractedLine
from app.models import ExtractionMethod
from app.retry import with_backoff

# Textract confidences are 0-100; the rest of the system works in 0-1.
_CONFIDENCE_SCALE = 100.0

_DATE_HEADERS = ("date", "transaction date", "posted", "posted date", "posting date")
_DESCRIPTION_HEADERS = ("description", "memo", "payee", "details", "narrative", "transaction")
_AMOUNT_HEADERS = ("amount", "value")
_DEBIT_HEADERS = ("debit", "withdrawal", "withdrawals", "money out")
_CREDIT_HEADERS = ("credit", "deposit", "deposits", "money in")

# Textract AnalyzeExpense SummaryField "Type.Text" values we understand,
# mapped to this app's field names (see ExtractionResult.fields).
_TEXTRACT_FIELD_MAP: dict[str, str] = {
    "VENDOR_NAME": "vendor",
    "TOTAL": "amount",
    "AMOUNT_DUE": "amount",
    "INVOICE_RECEIPT_DATE": "invoice_date",
}

FIELD_NAMES = ("vendor", "amount", "invoice_date")


@dataclass(frozen=True)
class ExtractedField:
    """One AnalyzeExpense summary field. Distinct from
    `app.extraction.types.ExtractedField` (which lacks a name and is keyed
    by position in a line's `fields` dict instead) -- this is the raw shape
    Textract itself returns."""

    name: str
    value: str
    confidence: float  # normalized 0.0-1.0


@dataclass(frozen=True)
class TextractExpenseResult:
    fields: list[ExtractedField]


class TextractClient(Protocol):
    """The narrow slice of Textract either pipeline path depends on."""

    def detect_text(self, document_bytes: bytes) -> list[str]:
        """Return raw text lines, cheapest-possible OCR pass."""
        ...

    def detect_text_async(self, s3_key: str) -> list[str]:
        """Same as `detect_text`, via the async job APIs for a multi-page PDF."""
        ...

    def analyze_expense(self, document_bytes: bytes) -> TextractExpenseResult:
        """Structured invoice/receipt extraction with per-field confidence."""
        ...

    def analyze_expense_async(self, s3_key: str) -> TextractExpenseResult:
        """Same as `analyze_expense`, via the async job APIs for a multi-page PDF."""
        ...

    def analyze_document(self, document_bytes: bytes) -> dict[str, Any]:
        """Return a raw Textract AnalyzeDocument response (TABLES feature)."""
        ...

    def analyze_document_async(self, s3_key: str) -> dict[str, Any]:
        """Same as `analyze_document`, via the async job APIs for a multi-page PDF."""
        ...


class TextractJobFailed(RuntimeError):
    """An async Textract job finished with a `FAILED` status."""


class TextractJobTimeout(RuntimeError):
    """An async Textract job did not reach a terminal status within the poll budget."""


def _page_count(document_bytes: bytes) -> int:
    """How many pages `document_bytes` has, for routing sync-vs-async.

    Only a PDF can have more than one page (an image is always effectively
    one), detected by magic bytes rather than trusting the Document's
    declared content-type -- uploads are allowed through as
    "application/octet-stream" (see app/documents.py), so content-type alone
    isn't reliable. A PDF that fails to parse falls back to 1 (the sync
    fast path): that preserves today's behaviour for whatever bytes look
    like a PDF but aren't fully valid, and any genuine problem still
    surfaces as a Textract error either way.
    """
    if not document_bytes.lstrip().startswith(b"%PDF"):
        return 1
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(document_bytes)).pages)
    except Exception:
        return 1


def requires_async(document_bytes: bytes) -> bool:
    """True when Textract's synchronous, bytes-based APIs would reject this
    document outright -- i.e. it is a PDF with more than one page (issue
    #18). A single-page PDF or an image keeps using the cheaper sync path.
    """
    return _page_count(document_bytes) > 1


def _is_transient_boto_error(exc: BaseException) -> bool:
    """Throttling, transient 5xx and connection-level failures are worth a
    retry; anything else (bad input, auth, an unrecognized document) is not
    -- retrying it would only delay the inevitable failure."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    try:
        from botocore.exceptions import ClientError, ConnectionError as BotoConnectionError

        if isinstance(exc, BotoConnectionError):
            return True
        if isinstance(exc, ClientError):
            code = exc.response.get("Error", {}).get("Code", "")
            return code in _THROTTLE_ERROR_CODES
    except ImportError:  # pragma: no cover - botocore ships with boto3
        pass
    return False


_THROTTLE_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ProvisionedThroughputExceededException",
        "TooManyRequestsException",
        "RequestTimeout",
        "RequestTimeoutException",
        "InternalServerError",
        "ServiceUnavailable",
        "ServiceUnavailableException",
    }
)

#: Terminal statuses an async Textract job's GetXxx response reports.
#: PARTIAL_SUCCESS still has a usable (if incomplete) result set -- treated
#: the same as SUCCEEDED rather than as a failure -- while FAILED has none.
_JOB_SUCCESS_STATUSES = frozenset({"SUCCEEDED", "PARTIAL_SUCCESS"})
_JOB_TERMINAL_STATUSES = _JOB_SUCCESS_STATUSES | {"FAILED"}

_DEFAULT_POLL_INTERVAL_SECONDS = 2.0
_DEFAULT_POLL_TIMEOUT_SECONDS = 600.0


def _lines_from_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    return [
        block["Text"] for block in blocks if block.get("BlockType") == "LINE" and "Text" in block
    ]


def _expense_fields_from_response(response: dict[str, Any]) -> list[ExtractedField]:
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
            fields.append(ExtractedField(name=mapped_name, value=value, confidence=confidence / 100.0))
    return fields


class AwsTextractClient:
    """The real client. Credentials come from the standard AWS env chain."""

    def __init__(
        self,
        region_name: str | None = None,
        *,
        bucket: str | None = None,
        poll_interval: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        poll_timeout: float = _DEFAULT_POLL_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._region_name = region_name
        self._client: Any = None
        #: The bucket the async Start... job APIs read the document from --
        #: the same bucket every uploaded Document is already stored in (see
        #: app/storage.py). Only needed on the async path; a deployment that
        #: never routes a multi-page PDF through this client can leave it
        #: unset.
        self._bucket = bucket
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._sleep = sleep

    def _boto_client(self) -> Any:
        if self._client is None:
            import boto3  # imported lazily: only a worker doing OCR needs it

            self._client = boto3.client("textract", region_name=self._region_name)
        return self._client

    def _retrying_call(self, op_name: str, call: Callable[[], Any]) -> Any:
        import botocore.exceptions

        return with_backoff(
            call,
            op_name=f"textract.{op_name}",
            retry_on=(botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError, TimeoutError, ConnectionError),
            should_retry=_is_transient_boto_error,
        )

    def _s3_object(self, s3_key: str) -> dict[str, str]:
        if not self._bucket:
            raise ValueError(
                "AwsTextractClient has no bucket configured -- required for the async job APIs"
            )
        return {"Bucket": self._bucket, "Name": s3_key}

    def _run_async_job(
        self,
        *,
        op_name: str,
        start_fn: Callable[[], dict[str, Any]],
        get_fn: Callable[[str, str | None], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Start an async Textract job, poll it to a terminal status, and
        return every page of its (possibly paginated) result.

        Raises `TextractJobFailed` on a `FAILED` status and `TextractJobTimeout`
        if no terminal status is reached within the poll budget -- both
        propagate out of the pipeline node as a normal exception, which
        `run_pipeline` already turns into a Document `failed` status rather
        than hanging or silently dropping data (issue #18, AC5).
        """
        start_response = self._retrying_call(f"{op_name}.start", start_fn)
        job_id = start_response["JobId"]

        elapsed = 0.0
        response = self._retrying_call(f"{op_name}.get", lambda: get_fn(job_id, None))
        while response.get("JobStatus") not in _JOB_TERMINAL_STATUSES:
            if elapsed >= self._poll_timeout:
                raise TextractJobTimeout(
                    f"{op_name} job {job_id} did not finish within {self._poll_timeout}s"
                )
            self._sleep(self._poll_interval)
            elapsed += self._poll_interval
            response = self._retrying_call(f"{op_name}.get", lambda: get_fn(job_id, None))

        if response.get("JobStatus") == "FAILED":
            reason = response.get("StatusMessage", "no status message")
            raise TextractJobFailed(f"{op_name} job {job_id} failed: {reason}")

        pages = [response]
        next_token = response.get("NextToken")
        while next_token:
            def _get_next_page(token: str = next_token) -> dict[str, Any]:
                return get_fn(job_id, token)

            page = self._retrying_call(f"{op_name}.get", _get_next_page)
            pages.append(page)
            next_token = page.get("NextToken")
        return pages

    def detect_text(self, document_bytes: bytes) -> list[str]:
        response = self._retrying_call(
            "detect_text",
            lambda: self._boto_client().detect_document_text(Document={"Bytes": document_bytes}),
        )
        return _lines_from_blocks(response.get("Blocks", []))

    def detect_text_async(self, s3_key: str) -> list[str]:
        pages = self._run_async_job(
            op_name="detect_text_async",
            start_fn=lambda: self._boto_client().start_document_text_detection(
                DocumentLocation={"S3Object": self._s3_object(s3_key)}
            ),
            get_fn=lambda job_id, next_token: self._boto_client().get_document_text_detection(
                JobId=job_id, **({"NextToken": next_token} if next_token else {})
            ),
        )
        lines: list[str] = []
        for page in pages:
            lines.extend(_lines_from_blocks(page.get("Blocks", [])))
        return lines

    def analyze_expense(self, document_bytes: bytes) -> TextractExpenseResult:
        response = self._retrying_call(
            "analyze_expense",
            lambda: self._boto_client().analyze_expense(Document={"Bytes": document_bytes}),
        )
        return TextractExpenseResult(fields=_expense_fields_from_response(response))

    def analyze_expense_async(self, s3_key: str) -> TextractExpenseResult:
        pages = self._run_async_job(
            op_name="analyze_expense_async",
            start_fn=lambda: self._boto_client().start_expense_analysis(
                DocumentLocation={"S3Object": self._s3_object(s3_key)}
            ),
            get_fn=lambda job_id, next_token: self._boto_client().get_expense_analysis(
                JobId=job_id, **({"NextToken": next_token} if next_token else {})
            ),
        )
        fields: list[ExtractedField] = []
        for page in pages:
            fields.extend(_expense_fields_from_response(page))
        return TextractExpenseResult(fields=fields)

    def analyze_document(self, document_bytes: bytes) -> dict[str, Any]:
        response = self._retrying_call(
            "analyze_document",
            lambda: self._boto_client().analyze_document(
                Document={"Bytes": document_bytes}, FeatureTypes=["TABLES"]
            ),
        )
        return dict(response)

    def analyze_document_async(self, s3_key: str) -> dict[str, Any]:
        pages = self._run_async_job(
            op_name="analyze_document_async",
            start_fn=lambda: self._boto_client().start_document_analysis(
                DocumentLocation={"S3Object": self._s3_object(s3_key)}, FeatureTypes=["TABLES"]
            ),
            get_fn=lambda job_id, next_token: self._boto_client().get_document_analysis(
                JobId=job_id, **({"NextToken": next_token} if next_token else {})
            ),
        )
        # Block Ids are unique across the whole document's pages, so a flat
        # concatenation is a valid merged response for parse_textract_tables.
        blocks: list[dict[str, Any]] = []
        for page in pages:
            blocks.extend(page.get("Blocks", []))
        return {"Blocks": blocks}


@lru_cache
def get_textract_client() -> TextractClient:
    from app.config import get_settings

    settings = get_settings()
    return AwsTextractClient(region_name=settings.aws_region, bucket=settings.minio_bucket)


def parse_textract_tables(response: dict[str, Any]) -> list[ExtractedLine]:
    """Turn a Textract AnalyzeDocument response into candidate statement lines.

    Every table in the response is examined; the first one whose header row
    looks like a statement (a date column plus an amount-ish column) wins.
    Cell confidence becomes the field's confidence, so a smudged amount is
    visibly less trustworthy than the date next to it.
    """
    blocks: list[dict[str, Any]] = list(response.get("Blocks", []))
    by_id = {block["Id"]: block for block in blocks if "Id" in block}

    for table in (b for b in blocks if b.get("BlockType") == "TABLE"):
        rows = _table_rows(table, by_id)
        if not rows:
            continue
        columns = _map_columns(rows[0])
        if columns is None:
            continue
        return _rows_to_lines(rows[1:], columns)
    return []


def _child_ids(block: dict[str, Any], relationship_type: str) -> list[str]:
    for relationship in block.get("Relationships", []) or []:
        if relationship.get("Type") == relationship_type:
            return list(relationship.get("Ids", []))
    return []


def _cell_text(cell: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    words = []
    for child_id in _child_ids(cell, "CHILD"):
        child = by_id.get(child_id, {})
        if child.get("BlockType") in ("WORD", "SELECTION_ELEMENT"):
            text = child.get("Text")
            if text:
                words.append(text)
    return " ".join(words).strip()


def _table_rows(
    table: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> list[list[tuple[str, float]]]:
    """Rows of (text, confidence 0-1) cells, ordered by Textract row/column index."""
    grid: dict[int, dict[int, tuple[str, float]]] = {}
    for cell_id in _child_ids(table, "CHILD"):
        cell = by_id.get(cell_id, {})
        if cell.get("BlockType") != "CELL":
            continue
        row_index = int(cell.get("RowIndex", 0))
        column_index = int(cell.get("ColumnIndex", 0))
        confidence = float(cell.get("Confidence", 0.0)) / _CONFIDENCE_SCALE
        grid.setdefault(row_index, {})[column_index] = (_cell_text(cell, by_id), confidence)

    rows = []
    for row_index in sorted(grid):
        columns = grid[row_index]
        rows.append([columns[i] for i in sorted(columns)])
    return rows


def _map_columns(header_row: list[tuple[str, float]]) -> dict[str, int] | None:
    headers = [text.strip().lower() for text, _ in header_row]

    def find(candidates: tuple[str, ...]) -> int | None:
        for index, header in enumerate(headers):
            if any(candidate == header or candidate in header for candidate in candidates):
                return index
        return None

    columns: dict[str, int] = {}
    date_index = find(_DATE_HEADERS)
    if date_index is None:
        return None
    columns["date"] = date_index

    description_index = find(_DESCRIPTION_HEADERS)
    if description_index is not None:
        columns["description"] = description_index

    amount_index = find(_AMOUNT_HEADERS)
    if amount_index is not None:
        columns["amount"] = amount_index
    else:
        debit_index = find(_DEBIT_HEADERS)
        credit_index = find(_CREDIT_HEADERS)
        if debit_index is None and credit_index is None:
            return None
        if debit_index is not None:
            columns["debit"] = debit_index
        if credit_index is not None:
            columns["credit"] = credit_index
    return columns


def _at(row: list[tuple[str, float]], index: int | None) -> tuple[str, float]:
    if index is None or index >= len(row):
        return ("", 0.0)
    return row[index]


def _rows_to_lines(rows: list[list[tuple[str, float]]], columns: dict[str, int]) -> list[ExtractedLine]:
    lines: list[ExtractedLine] = []
    for row in rows:
        if not any(text.strip() for text, _ in row):
            continue
        date_text, date_confidence = _at(row, columns.get("date"))
        description_text, description_confidence = _at(row, columns.get("description"))
        if "amount" in columns:
            amount_text, amount_confidence = _at(row, columns["amount"])
        else:
            debit_text, debit_confidence = _at(row, columns.get("debit"))
            credit_text, credit_confidence = _at(row, columns.get("credit"))
            if debit_text.strip():
                amount_text = debit_text if debit_text.startswith("-") else f"-{debit_text}"
                amount_confidence = debit_confidence
            else:
                amount_text, amount_confidence = credit_text, credit_confidence

        if not date_text.strip() and not amount_text.strip():
            continue  # a totals/footer row, not a transaction

        lines.append(
            ExtractedLine(
                line_number=len(lines) + 1,
                fields={
                    "date": _field(date_text, date_confidence),
                    "description": _field(description_text, description_confidence),
                    "amount": _field(amount_text, amount_confidence),
                },
            )
        )
    return lines


def _field(text: str, confidence: float) -> LineField:
    cleaned = text.strip()
    return LineField(
        value=cleaned or None,
        # An empty cell is not "confidently empty" -- it is a field the OCR
        # failed to read, so it must fall below any threshold and be refined.
        confidence=confidence if cleaned else 0.0,
        method=ExtractionMethod.ocr,
    )
