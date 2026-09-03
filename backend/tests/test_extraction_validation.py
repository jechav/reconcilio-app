"""Schema validation is the gate every ingestion path must clear."""

from datetime import date
from decimal import Decimal

from app.extraction.types import ExtractedLine, structured_field
from app.extraction.validation import validate_lines


def _line(line_number: int, date_value: str | None, description: str | None, amount: str | None):
    return ExtractedLine(
        line_number=line_number,
        fields={
            "date": structured_field(date_value),
            "description": structured_field(description),
            "amount": structured_field(amount),
        },
    )


def test_validate_parses_common_date_and_amount_shapes():
    outcomes = validate_lines(
        [
            _line(1, "2026-01-04", "COFFEE  ROASTERS ", "-4.50"),
            _line(2, "01/06/2026", "CLIENT PAYMENT", "$1,200.00"),
            _line(3, "20260109", "OFFICE SUPPLIES", "(89.99)"),
        ]
    )

    assert all(outcome.ok for outcome in outcomes)
    assert outcomes[0].line.txn_date == date(2026, 1, 4)
    assert outcomes[0].line.description == "COFFEE ROASTERS"  # whitespace collapsed
    assert outcomes[1].line.amount == Decimal("1200.00")
    # Accounting parentheses mean a negative amount.
    assert outcomes[2].line.amount == Decimal("-89.99")


def test_validate_rejects_unparseable_lines_without_losing_the_others():
    outcomes = validate_lines(
        [
            _line(1, "not-a-date", "COFFEE", "-4.50"),
            _line(2, "2026-01-06", "CLIENT PAYMENT", "1200.00"),
            _line(3, "2026-01-07", "MISSING AMOUNT", None),
            _line(4, "2026-01-08", None, "-1.00"),
        ]
    )

    assert [outcome.ok for outcome in outcomes] == [False, True, False, False]
    assert outcomes[0].error is not None
