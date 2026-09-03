"""CSV/OFX structured parsing -- no external services, tested for real."""

import pytest

from app.extraction.structured import (
    StructuredParseError,
    is_structured,
    parse_structured,
)
from app.models import ExtractionMethod

CSV_STATEMENT = b"""Date,Description,Amount
2026-01-04,COFFEE ROASTERS,-4.50
2026-01-06,CLIENT PAYMENT ACME,1200.00
2026-01-09,OFFICE SUPPLIES CO,-89.99
"""

CSV_DEBIT_CREDIT = b"""Posted Date,Memo,Debit,Credit
01/04/2026,COFFEE ROASTERS,4.50,
01/06/2026,CLIENT PAYMENT,,1200.00
"""

OFX_STATEMENT = b"""OFXHEADER:100
DATA:OFXSGML

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260104120000[-5:EST]
<TRNAMT>-4.50
<FITID>0001
<NAME>COFFEE ROASTERS
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260106
<TRNAMT>1200.00
<FITID>0002
<NAME>CLIENT PAYMENT
<MEMO>INVOICE 42
</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""

OFX_XML_STATEMENT = b"""<?xml version="1.0"?>
<OFX><BANKTRANLIST>
<STMTTRN><DTPOSTED>20260201</DTPOSTED><TRNAMT>-25.00</TRNAMT><NAME>PARKING</NAME></STMTTRN>
</BANKTRANLIST></OFX>
"""


def test_is_structured_recognizes_csv_and_ofx_only():
    assert is_structured("statement.csv")
    assert is_structured("STATEMENT.OFX")
    assert is_structured("statement.qfx")
    assert not is_structured("statement.pdf")
    assert not is_structured("scan.png")


def test_parse_csv_produces_one_line_per_row_at_full_confidence():
    lines = parse_structured("statement.csv", CSV_STATEMENT)

    assert [line.line_number for line in lines] == [1, 2, 3]
    assert [line.value("amount") for line in lines] == ["-4.50", "1200.00", "-89.99"]
    assert lines[0].value("description") == "COFFEE ROASTERS"
    for line in lines:
        for field in line.fields.values():
            assert field.confidence == 1.0
            assert field.method == ExtractionMethod.structured_parse


def test_parse_csv_normalizes_debit_credit_columns_to_signed_amounts():
    lines = parse_structured("statement.csv", CSV_DEBIT_CREDIT)

    assert [line.value("amount") for line in lines] == ["-4.50", "1200.00"]
    assert lines[0].value("date") == "01/04/2026"


def test_parse_csv_rejects_a_file_without_an_amount_column():
    with pytest.raises(StructuredParseError):
        parse_structured("statement.csv", b"Date,Description\n2026-01-04,COFFEE\n")


def test_parse_csv_rejects_an_empty_file():
    with pytest.raises(StructuredParseError):
        parse_structured("statement.csv", b"")


def test_parse_ofx_reads_sgml_transactions_and_normalizes_dates():
    lines = parse_structured("statement.ofx", OFX_STATEMENT)

    assert len(lines) == 2
    assert lines[0].value("date") == "2026-01-04"
    assert lines[0].value("amount") == "-4.50"
    assert lines[0].value("description") == "COFFEE ROASTERS"
    # NAME and MEMO are both meaningful, so both are kept.
    assert lines[1].value("description") == "CLIENT PAYMENT - INVOICE 42"
    assert lines[1].value("date") == "2026-01-06"


def test_parse_ofx_also_reads_xml_style_ofx_2():
    lines = parse_structured("statement.ofx", OFX_XML_STATEMENT)

    assert len(lines) == 1
    assert lines[0].value("date") == "2026-02-01"
    assert lines[0].value("amount") == "-25.00"
    assert lines[0].value("description") == "PARKING"


def test_parse_ofx_rejects_a_file_with_no_transactions():
    with pytest.raises(StructuredParseError):
        parse_structured("statement.ofx", b"<OFX></OFX>")


def test_parse_structured_rejects_a_non_structured_extension():
    with pytest.raises(StructuredParseError):
        parse_structured("statement.pdf", b"%PDF-1.4")
