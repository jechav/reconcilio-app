"""Pydantic schema the extraction output must satisfy before persisting
(issue #3, AC5)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ExtractionSchema(BaseModel):
    vendor: str | None = None
    amount: Decimal | None = None
    invoice_date: date | None = None
