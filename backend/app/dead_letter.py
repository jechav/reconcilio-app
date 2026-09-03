"""The dead-letter path for a Celery task that keeps failing on the same
Document (issue #7, AC2) -- a `DeadLetterTask` row instead of retrying
forever or vanishing silently. Insert-only; `error` is truncated to the
model's column width so an unusually long exception message never breaks
the write.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import DeadLetterTask

_MAX_ERROR_LEN = 2000


def record_dead_letter(
    db: Session,
    *,
    document_id: uuid.UUID,
    org_id: uuid.UUID | None,
    task_name: str,
    error: str,
    attempts: int,
) -> DeadLetterTask:
    entry = DeadLetterTask(
        org_id=org_id,
        document_id=document_id,
        task_name=task_name,
        error=error[:_MAX_ERROR_LEN],
        attempts=attempts,
    )
    db.add(entry)
    return entry
