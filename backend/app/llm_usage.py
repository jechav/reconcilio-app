"""Per-tenant LLM usage tracking and querying (issue #7, AC5).

`record_llm_call` is invoked once per actual refiner call from
app/pipeline.py -- never for `NullRefiner`/no-op refinement, since no call
was made and so no cost was incurred. `usage_summary` aggregates it back per
Organization for the `GET /orgs/me/llm-usage` endpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LlmUsage


def record_llm_call(
    db: Session,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID | None,
    provider: str,
    model: str,
) -> None:
    db.add(LlmUsage(org_id=org_id, document_id=document_id, provider=provider, model=model, calls=1))


@dataclass(frozen=True)
class LlmUsageTotal:
    provider: str
    model: str
    calls: int


def usage_summary(db: Session, org_id: uuid.UUID) -> list[LlmUsageTotal]:
    """Total calls per (provider, model) for one Organization, most-used first."""
    stmt = (
        select(LlmUsage.provider, LlmUsage.model, func.sum(LlmUsage.calls))
        .where(LlmUsage.org_id == org_id)
        .group_by(LlmUsage.provider, LlmUsage.model)
        .order_by(func.sum(LlmUsage.calls).desc())
    )
    return [
        LlmUsageTotal(provider=provider, model=model, calls=int(calls))
        for provider, model, calls in db.execute(stmt).all()
    ]
