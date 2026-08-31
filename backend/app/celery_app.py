from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("taxdocs", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="taxdocs.ping")
def ping() -> str:
    return "pong"
