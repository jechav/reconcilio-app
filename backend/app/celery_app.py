import uuid

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("reconcilio", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="reconcilio.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="reconcilio.process_document")
def process_document(document_id: str) -> str:
    # Imported lazily so importing celery_app (e.g. at FastAPI startup) never
    # pulls in the DB/pipeline modules unless a worker actually runs a task.
    from app.database import SessionLocal
    from app.pipeline import run_pipeline

    db = SessionLocal()
    try:
        document = run_pipeline(uuid.UUID(document_id), db)
        return document.status.value
    finally:
        db.close()
