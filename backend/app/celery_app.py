"""The Celery app and its one real task, `process_document`.

Reliability (issue #7): a transient failure inside `run_pipeline` (a
Textract/LLM call that ultimately exhausted its own retries, a dropped DB
connection, ...) retries the whole task in-process with backoff (via
`app.retry.with_backoff`), up to `MAX_ATTEMPTS` total tries. A Document that
still fails after that is routed to the dead-letter table
(`app/dead_letter.py`) instead of retrying forever or silently vanishing --
its Document.status is left `failed` (set by `run_pipeline` itself) so it
is visible in the UI too.

The retry loop is driven directly rather than through Celery's own
`self.retry`/broker redelivery: `self.retry()` reschedules the task via the
broker (a real countdown delay in production, and -- under
`task_always_eager`, as tests run with -- simply re-raises rather than
looping), which makes deterministic tests of "retried, then succeeded" and
"retries exhausted, dead-lettered" awkward without a live broker. Driving
the loop with `with_backoff` keeps both paths testable with an injectable
sleep and identical behaviour in eager tests and a real worker.
"""

import logging
import uuid

from celery import Celery

from app.config import get_settings
from app.logging_config import configure_logging
from app.retry import with_backoff

configure_logging()

settings = get_settings()

celery_app = Celery("reconcilio", broker=settings.redis_url, backend=settings.redis_url)

logger = logging.getLogger("reconcilio.celery")

#: Total attempts (the first try plus retries) before a Document's
#: processing failure is routed to the dead-letter table (issue #7, AC2).
MAX_ATTEMPTS = 3
#: Base delay in seconds between attempts (issue #7, AC1); attempt N waits
#: roughly RETRY_BACKOFF * 2**(N-1).
RETRY_BACKOFF = 5.0


@celery_app.task(name="reconcilio.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="reconcilio.process_document", bind=True)
def process_document(self, document_id: str) -> str:
    # Imported lazily so importing celery_app (e.g. at FastAPI startup) never
    # pulls in the DB/pipeline modules unless a worker actually runs a task.
    from app.database import SessionLocal
    from app.dead_letter import record_dead_letter
    from app.models import Document
    from app.pipeline import run_pipeline

    db = SessionLocal()

    def _attempt() -> str:
        document = run_pipeline(uuid.UUID(document_id), db)
        return document.status.value

    def _on_retry(exc: BaseException) -> None:
        db.rollback()
        logger.warning(
            "task.process_document.retrying",
            extra={"document_id": document_id, "error_type": type(exc).__name__},
        )

    try:
        return _run_with_retry(_attempt, on_retry=_on_retry)
    except Exception as exc:
        # Retries exhausted -- dead-letter instead of raising past Celery
        # (which would just drop the task) or retrying forever (AC2).
        db.rollback()
        document = db.get(Document, uuid.UUID(document_id))
        org_id = document.org_id if document is not None else None
        record_dead_letter(
            db,
            document_id=uuid.UUID(document_id),
            org_id=org_id,
            task_name=self.name,
            error=str(exc),
            attempts=MAX_ATTEMPTS,
        )
        db.commit()
        logger.error(
            "task.process_document.dead_lettered",
            extra={"document_id": document_id, "attempts": MAX_ATTEMPTS, "error_type": type(exc).__name__},
        )
        return "dead_lettered"
    finally:
        db.close()


def _run_with_retry(attempt_fn, *, on_retry) -> str:
    def _tracked() -> str:
        try:
            return attempt_fn()
        except Exception as exc:
            on_retry(exc)
            raise

    return with_backoff(
        _tracked,
        op_name="celery.process_document",
        max_attempts=MAX_ATTEMPTS,
        base_delay=RETRY_BACKOFF,
    )
