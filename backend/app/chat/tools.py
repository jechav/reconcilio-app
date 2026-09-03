"""The chat agent's two read-only, Organization-scoped tools (issue #11).

`query_transactions` is the structured-query tool over the calling
Organization's own Transactions; `search_embeddings` is the vector-search
tool over Document/Transaction embeddings. Both take `org_id` as a required
keyword argument and route every query through `org_scoped_select` (see
app/scoping.py) or an explicit `Embedding.org_id == org_id` filter, so
neither tool can be made to return another Organization's data no matter
what the agent asks for -- this is what issue #11's isolation acceptance
criterion tests directly (tests/test_chat.py).

Both are plain functions, not classes: there is nothing here to inject or
fake beyond the `Session` and `EmbeddingClient` already passed in, and a
plain function is the most directly unit-testable shape (CLAUDE.md: "keep
the tool surface minimal and directly testable rather than over-engineering
a generic agent framework").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.embed import EmbeddingClient
from app.models import Embedding, EmbeddingSourceType, Transaction
from app.scoping import org_scoped_select

DEFAULT_TRANSACTION_LIMIT = 20
DEFAULT_VECTOR_SEARCH_TOP_K = 5


def query_transactions(
    db: Session,
    *,
    org_id: uuid.UUID,
    transaction_ids: list[uuid.UUID] | None = None,
    document_ids: list[uuid.UUID] | None = None,
    description_contains: str | None = None,
    start_date: date_type | None = None,
    end_date: date_type | None = None,
    limit: int = DEFAULT_TRANSACTION_LIMIT,
) -> list[Transaction]:
    """Structured-query tool: filter the calling Organization's Transactions.

    Every argument is optional and stacks with the others, same convention
    as GET /audit-log (app/routers/audit.py). `transaction_ids`/`document_ids`
    are how the chat agent resolves the full row (amount/date/category) for
    Transactions a vector-search hit only returned by id -- always re-scoped
    by org_id here rather than trusted from the hit, so a stale or forged id
    can never leak a Transaction from another Organization.
    """
    if transaction_ids is not None and not transaction_ids:
        return []
    if document_ids is not None and not document_ids:
        return []

    stmt = org_scoped_select(Transaction, org_id)
    if transaction_ids is not None:
        stmt = stmt.where(Transaction.id.in_(transaction_ids))
    if document_ids is not None:
        stmt = stmt.where(Transaction.document_id.in_(document_ids))
    if description_contains:
        stmt = stmt.where(Transaction.description.ilike(f"%{description_contains}%"))
    if start_date is not None:
        stmt = stmt.where(Transaction.txn_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Transaction.txn_date <= end_date)
    stmt = stmt.order_by(Transaction.txn_date.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


@dataclass(frozen=True)
class EmbeddingHit:
    embedding_id: uuid.UUID
    source_type: EmbeddingSourceType
    source_id: uuid.UUID
    document_id: uuid.UUID
    transaction_id: uuid.UUID | None
    content: str
    distance: float


def search_embeddings(
    db: Session,
    embedding_client: EmbeddingClient,
    *,
    org_id: uuid.UUID,
    query_text: str,
    top_k: int = DEFAULT_VECTOR_SEARCH_TOP_K,
) -> list[EmbeddingHit]:
    """Vector-search tool: the Organization's Document/Transaction embeddings
    nearest `query_text` by cosine distance.

    Rows with no vector (`NullEmbeddingClient` -- no LLM configured, see
    app/extraction/embed.py) are excluded rather than surfaced with a
    meaningless distance. If `embedding_client.embed` itself returns None
    (also the no-LLM case, or empty `query_text`), the search can't run at
    all and comes back empty -- the structured-query tool is still available
    to the agent in that case.
    """
    query_vector = embedding_client.embed(query_text)
    if query_vector is None:
        return []

    stmt = (
        select(Embedding, Embedding.vector.cosine_distance(query_vector).label("distance"))
        .where(Embedding.org_id == org_id)
        .where(Embedding.vector.is_not(None))
        .order_by("distance")
        .limit(top_k)
    )
    rows = db.execute(stmt).all()
    return [
        EmbeddingHit(
            embedding_id=row.Embedding.id,
            source_type=row.Embedding.source_type,
            source_id=row.Embedding.source_id,
            document_id=row.Embedding.document_id,
            transaction_id=row.Embedding.transaction_id,
            content=row.Embedding.content,
            distance=float(row.distance),
        )
        for row in rows
    ]
