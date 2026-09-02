"""The currency the extraction nodes pass between each other.

Every ingestion path (Textract OCR, LLM refinement, CSV/OFX structured
parse) produces the same shape: a list of `ExtractedLine`, each carrying one
`ExtractedField` per field with its own value, confidence and method. That
uniformity is what makes "which field came from where" answerable for every
Document regardless of how it was ingested (CONTEXT.md, ExtractionResult).
"""

from dataclasses import dataclass, field as dataclass_field
from typing import Any

from app.models import ExtractionMethod

#: The fields a bank-statement line is made of, in audit/display order.
LINE_FIELDS = ("date", "description", "amount")


@dataclass(frozen=True)
class ExtractedField:
    value: str | None
    confidence: float
    method: ExtractionMethod

    def to_json(self) -> dict[str, Any]:
        return {"value": self.value, "confidence": self.confidence, "method": self.method.value}


@dataclass
class ExtractedLine:
    """One candidate line item, before schema validation."""

    line_number: int
    fields: dict[str, ExtractedField] = dataclass_field(default_factory=dict)

    def value(self, name: str) -> str | None:
        found = self.fields.get(name)
        return found.value if found is not None else None

    @property
    def min_confidence(self) -> float:
        """The line is only as trustworthy as its weakest field."""
        if not self.fields:
            return 0.0
        return min(f.confidence for f in self.fields.values())

    @property
    def weakest_method(self) -> ExtractionMethod:
        """Method of the least-confident field -- the line-level summary."""
        if not self.fields:
            return ExtractionMethod.ocr
        return min(self.fields.values(), key=lambda f: f.confidence).method

    def low_confidence_fields(self, threshold: float) -> list[str]:
        return [name for name, f in self.fields.items() if f.confidence < threshold]

    def to_json(self) -> dict[str, Any]:
        return {name: f.to_json() for name, f in self.fields.items()}


def structured_field(value: str | None) -> ExtractedField:
    """CSV/OFX is already machine-readable, so it is exact by definition."""
    return ExtractedField(value=value, confidence=1.0, method=ExtractionMethod.structured_parse)
