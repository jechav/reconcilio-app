"""Structured-parse path: CSV and OFX bank statements.

A CSV or OFX statement is already machine-readable, so running it through
OCR would only add cost and uncertainty to data that is exact. This module
is the dedicated parse step that replaces classify/ocr_extract/llm_refine
for those formats; it needs no external service and no credentials, and
every field it produces is `structured_parse` at confidence 1.0.
"""

import csv
import io
import re
from datetime import datetime

from app.extraction.types import ExtractedLine, structured_field


class StructuredParseError(ValueError):
    """The file could not be parsed as a bank statement at all."""


CSV_EXTENSIONS = {".csv"}
OFX_EXTENSIONS = {".ofx", ".qfx"}
STRUCTURED_EXTENSIONS = CSV_EXTENSIONS | OFX_EXTENSIONS

# Banks disagree wildly about column names; match on a normalized header.
_DATE_HEADERS = ("date", "transactiondate", "posteddate", "postingdate", "dateposted", "valuedate")
_DESCRIPTION_HEADERS = ("description", "memo", "payee", "narrative", "details", "name", "reference")
_AMOUNT_HEADERS = ("amount", "value", "transactionamount")
_DEBIT_HEADERS = ("debit", "withdrawal", "withdrawals", "moneyout", "paidout")
_CREDIT_HEADERS = ("credit", "deposit", "deposits", "moneyin", "paidin")


def extension_of(filename: str) -> str:
    lowered = filename.lower()
    return "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""


def is_structured(filename: str) -> bool:
    return extension_of(filename) in STRUCTURED_EXTENSIONS


def parse_structured(filename: str, data: bytes) -> list[ExtractedLine]:
    """Parse a CSV or OFX statement into one ExtractedLine per statement line."""
    extension = extension_of(filename)
    text = _decode(data)
    if extension in CSV_EXTENSIONS:
        return parse_csv(text)
    if extension in OFX_EXTENSIONS:
        return parse_ofx(text)
    raise StructuredParseError(f"'{extension}' is not a structured statement format")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise StructuredParseError("File is not decodable text")


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def _match_header(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized = {_normalize_header(h): h for h in headers}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def parse_csv(text: str) -> list[ExtractedLine]:
    """Map a header row onto date/description/amount, then read the rows.

    Handles both a single signed `amount` column and the separate
    debit/credit column pair banks often export instead (debits are
    normalized to negative amounts so downstream code sees one convention).
    """
    stripped = text.strip()
    if not stripped:
        raise StructuredParseError("CSV statement is empty")

    reader = csv.DictReader(io.StringIO(stripped))
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        raise StructuredParseError("CSV statement has no header row")

    date_col = _match_header(headers, _DATE_HEADERS)
    description_col = _match_header(headers, _DESCRIPTION_HEADERS)
    amount_col = _match_header(headers, _AMOUNT_HEADERS)
    debit_col = _match_header(headers, _DEBIT_HEADERS)
    credit_col = _match_header(headers, _CREDIT_HEADERS)

    if date_col is None:
        raise StructuredParseError("CSV statement has no recognizable date column")
    if amount_col is None and debit_col is None and credit_col is None:
        raise StructuredParseError("CSV statement has no recognizable amount column")

    lines: list[ExtractedLine] = []
    for row in reader:
        if not any((value or "").strip() for value in row.values()):
            continue  # blank filler row
        amount = (
            (row.get(amount_col) or "").strip()
            if amount_col is not None
            else _debit_credit_amount(row.get(debit_col or ""), row.get(credit_col or ""))
        )
        lines.append(
            ExtractedLine(
                line_number=len(lines) + 1,
                fields={
                    "date": structured_field((row.get(date_col) or "").strip() or None),
                    "description": structured_field(
                        ((row.get(description_col) or "").strip() if description_col else "") or None
                    ),
                    "amount": structured_field(amount or None),
                },
            )
        )

    if not lines:
        raise StructuredParseError("CSV statement contains no transaction rows")
    return lines


def _debit_credit_amount(debit: str | None, credit: str | None) -> str:
    debit = (debit or "").strip()
    credit = (credit or "").strip()
    if debit:
        return debit if debit.startswith("-") else f"-{debit}"
    return credit


_OFX_TRANSACTION = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.IGNORECASE | re.DOTALL)


def _ofx_tag(block: str, tag: str) -> str | None:
    """Read one tag out of an OFX block.

    OFX 1.x is SGML with optional closing tags (`<TRNAMT>-12.50` runs to the
    next tag or newline) while OFX 2.x is real XML, so accept both shapes.
    """
    match = re.search(rf"<{tag}>(.*?)(?:</{tag}>|<|$)", block, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None


def _ofx_date(raw: str | None) -> str | None:
    """OFX dates are YYYYMMDD with an optional time/timezone suffix."""
    if raw is None:
        return None
    digits = raw.strip()[:8]
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return raw.strip()  # let schema validation reject it, with the raw value visible


def parse_ofx(text: str) -> list[ExtractedLine]:
    blocks = _OFX_TRANSACTION.findall(text)
    if not blocks:
        raise StructuredParseError("OFX statement contains no <STMTTRN> transactions")

    lines: list[ExtractedLine] = []
    for block in blocks:
        name = _ofx_tag(block, "NAME")
        memo = _ofx_tag(block, "MEMO")
        description = " - ".join(part for part in (name, memo) if part) or None
        lines.append(
            ExtractedLine(
                line_number=len(lines) + 1,
                fields={
                    "date": structured_field(_ofx_date(_ofx_tag(block, "DTPOSTED"))),
                    "description": structured_field(description),
                    "amount": structured_field(_ofx_tag(block, "TRNAMT")),
                },
            )
        )
    return lines
