"""Turning a Textract TABLES response into candidate statement lines."""

from app.extraction.textract import parse_textract_tables
from app.models import ExtractionMethod
from tests.textract_fixtures import textract_table_response


def test_parses_a_statement_table_with_per_field_confidence():
    response = textract_table_response(
        [
            [("Date", 99.0), ("Description", 99.0), ("Amount", 99.0)],
            [("2026-01-04", 98.5), ("COFFEE ROASTERS", 97.0), ("-4.50", 62.0)],
            [("2026-01-06", 99.1), ("CLIENT PAYMENT", 96.0), ("1200.00", 95.0)],
        ]
    )

    lines = parse_textract_tables(response)

    assert len(lines) == 2
    assert lines[0].value("date") == "2026-01-04"
    assert lines[0].fields["amount"].confidence == 0.62
    assert lines[0].fields["amount"].method == ExtractionMethod.ocr
    # The line is only as trustworthy as its weakest field.
    assert lines[0].min_confidence == 0.62
    assert lines[1].min_confidence == 0.95


def test_maps_separate_debit_and_credit_columns_to_a_signed_amount():
    response = textract_table_response(
        [
            [("Posted Date", 99.0), ("Details", 99.0), ("Withdrawal", 99.0), ("Deposit", 99.0)],
            [("2026-01-04", 99.0), ("COFFEE", 99.0), ("4.50", 90.0), ("", 0.0)],
            [("2026-01-06", 99.0), ("PAYMENT", 99.0), ("", 0.0), ("1200.00", 93.0)],
        ]
    )

    lines = parse_textract_tables(response)

    assert [line.value("amount") for line in lines] == ["-4.50", "1200.00"]


def test_an_unreadable_cell_is_zero_confidence_rather_than_confidently_blank():
    response = textract_table_response(
        [
            [("Date", 99.0), ("Description", 99.0), ("Amount", 99.0)],
            [("2026-01-04", 99.0), ("", 88.0), ("-4.50", 99.0)],
        ]
    )

    lines = parse_textract_tables(response)

    assert lines[0].value("description") is None
    assert lines[0].fields["description"].confidence == 0.0
    assert lines[0].low_confidence_fields(0.8) == ["description"]


def test_returns_nothing_when_no_table_looks_like_a_statement():
    response = textract_table_response(
        [[("Item", 99.0), ("Qty", 99.0)], [("Widget", 99.0), ("2", 99.0)]]
    )

    assert parse_textract_tables(response) == []
