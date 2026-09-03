"""Schema validation: the gate every path must clear before persisting.

Textract, the LLM and the structured parsers all emit loose strings. This
module is the single place those strings become a typed, persistable line --
so a malformed amount or an unparseable date can never reach the database,
whichever ingestion path produced it.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field, field_validator

from app.extraction.types import ExtractedLine

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m/%d/%y", "%d-%m-%Y", "%Y%m%d")


class BankStatementLine(BaseModel):
    """One validated bank-statement line, ready to become a Transaction."""

    line_number: int = Field(ge=1)
    txn_date: date
    description: str = Field(min_length=1, max_length=512)
    amount: Decimal

    @field_validator("txn_date", mode="before")
    @classmethod
    def _parse_date(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        from datetime import datetime

        raw = value.strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Unrecognized date format: {value!r}")

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        # Strip the currency symbols, thousands separators and accounting
        # parentheses that statements are full of: "$(1,234.50)" -> -1234.50
        raw = value.strip().replace(",", "").replace("$", "").replace(" ", "").replace(" ", "")
        negative = raw.startswith("(") and raw.endswith(")")
        if negative:
            raw = raw[1:-1]
        if not raw:
            raise ValueError("Amount is empty")
        try:
            amount = Decimal(raw)
        except InvalidOperation:
            raise ValueError(f"Unrecognized amount: {value!r}") from None
        return -amount if negative else amount

    @field_validator("description", mode="before")
    @classmethod
    def _clean_description(cls, value: object) -> object:
        if isinstance(value, str):
            return " ".join(value.split())[:512]
        return value


class ValidationOutcome(BaseModel):
    """A validated line, or the reason its raw counterpart was rejected."""

    line_number: int
    line: BankStatementLine | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.line is not None


def validate_line(
    raw: ExtractedLine,
    *,
    date_field: str = "date",
    description_field: str = "description",
    amount_field: str = "amount",
) -> ValidationOutcome:
    """Validate one line, reading its date/description/amount from whichever
    field names that ingestion path uses. Bank-statement lines use the
    generic `date`/`description`/`amount`; the invoice/receipt path (always
    a single line) passes its own `invoice_date`/`vendor`/`amount` field
    names instead -- see app/pipeline.py."""
    try:
        line = BankStatementLine(
            line_number=raw.line_number,
            txn_date=raw.value(date_field),  # type: ignore[arg-type]
            description=raw.value(description_field),  # type: ignore[arg-type]
            amount=raw.value(amount_field),  # type: ignore[arg-type]
        )
    except Exception as exc:  # pydantic ValidationError, or a bad raw value
        return ValidationOutcome(line_number=raw.line_number, error=_summarize(exc))
    return ValidationOutcome(line_number=raw.line_number, line=line)


def validate_lines(raw_lines: list[ExtractedLine]) -> list[ValidationOutcome]:
    return [validate_line(raw) for raw in raw_lines]


def _summarize(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:500]
