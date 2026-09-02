"""OCR path: AWS Textract table extraction for PDF/image bank statements.

A bank statement is a table, so `AnalyzeDocument` with the TABLES feature is
the right Textract call -- it gives us cells with per-cell confidence, which
is exactly the granularity the audit trail wants (one confidence per field,
not one per document).

The client is behind a Protocol so the pipeline never imports boto3 and the
tests can substitute a fake at the network boundary. There are no AWS
credentials in the test/dev sandbox, so `AwsTextractClient` is exercised
only against recorded Textract response blocks, never live.
"""

from typing import Any, Protocol

from app.extraction.types import ExtractedField, ExtractedLine
from app.models import ExtractionMethod

# Textract confidences are 0-100; the rest of the system works in 0-1.
_CONFIDENCE_SCALE = 100.0

_DATE_HEADERS = ("date", "transaction date", "posted", "posted date", "posting date")
_DESCRIPTION_HEADERS = ("description", "memo", "payee", "details", "narrative", "transaction")
_AMOUNT_HEADERS = ("amount", "value")
_DEBIT_HEADERS = ("debit", "withdrawal", "withdrawals", "money out")
_CREDIT_HEADERS = ("credit", "deposit", "deposits", "money in")


class TextractClient(Protocol):
    """The narrow slice of Textract this pipeline depends on."""

    def analyze_document(self, document_bytes: bytes) -> dict[str, Any]:
        """Return a raw Textract AnalyzeDocument response (TABLES feature)."""


class AwsTextractClient:
    """The real client. Credentials come from the standard AWS env chain."""

    def __init__(self, region_name: str | None = None) -> None:
        self._region_name = region_name
        self._client: Any = None

    def _boto_client(self) -> Any:
        if self._client is None:
            import boto3  # imported lazily: only a worker doing OCR needs it

            self._client = boto3.client("textract", region_name=self._region_name)
        return self._client

    def analyze_document(self, document_bytes: bytes) -> dict[str, Any]:
        response = self._boto_client().analyze_document(
            Document={"Bytes": document_bytes}, FeatureTypes=["TABLES"]
        )
        return dict(response)


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


def _field(text: str, confidence: float) -> ExtractedField:
    cleaned = text.strip()
    return ExtractedField(
        value=cleaned or None,
        # An empty cell is not "confidently empty" -- it is a field the OCR
        # failed to read, so it must fall below any threshold and be refined.
        confidence=confidence if cleaned else 0.0,
        method=ExtractionMethod.ocr,
    )
