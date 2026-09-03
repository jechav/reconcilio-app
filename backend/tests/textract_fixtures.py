"""Builders for Textract AnalyzeDocument responses.

There are no AWS credentials in this environment, so the Textract client is
faked at the network boundary and the *parsing* of a realistically-shaped
response (TABLE -> CELL -> WORD blocks with per-cell confidence) is what the
tests exercise.
"""

import uuid
from typing import Any


def textract_table_response(rows: list[list[tuple[str, float]]]) -> dict[str, Any]:
    """Build a TABLES-feature response from rows of (cell text, confidence 0-100)."""
    blocks: list[dict[str, Any]] = []
    cell_ids: list[str] = []

    for row_index, row in enumerate(rows, start=1):
        for column_index, (text, confidence) in enumerate(row, start=1):
            cell_id = str(uuid.uuid4())
            word_ids = []
            for word in text.split():
                word_id = str(uuid.uuid4())
                word_ids.append(word_id)
                blocks.append(
                    {"Id": word_id, "BlockType": "WORD", "Text": word, "Confidence": confidence}
                )
            cell: dict[str, Any] = {
                "Id": cell_id,
                "BlockType": "CELL",
                "RowIndex": row_index,
                "ColumnIndex": column_index,
                "Confidence": confidence,
            }
            if word_ids:
                cell["Relationships"] = [{"Type": "CHILD", "Ids": word_ids}]
            blocks.append(cell)
            cell_ids.append(cell_id)

    blocks.append(
        {
            "Id": str(uuid.uuid4()),
            "BlockType": "TABLE",
            "Relationships": [{"Type": "CHILD", "Ids": cell_ids}],
        }
    )
    return {"Blocks": blocks}


class FakeTextractClient:
    """Returns a canned response and records what it was asked to read."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[bytes] = []

    def analyze_document(self, document_bytes: bytes) -> dict[str, Any]:
        self.calls.append(document_bytes)
        return self.response
