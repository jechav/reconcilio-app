from litestar import Response, Router, get
from litestar.di import NamedDependency
from litestar.status_codes import HTTP_503_SERVICE_UNAVAILABLE
from sqlalchemy import text
from sqlalchemy.orm import Session


@get("/health", sync_to_thread=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@get("/ready", sync_to_thread=True)
def ready(db: NamedDependency[Session]) -> Response[dict[str, str]]:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return Response(
            {"status": "not ready", "database": "unreachable"},
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"status": "ready", "database": "ok"})


router = Router(
    path="/",
    route_handlers=[health, ready],
    tags=["health"],
    opt={"exclude_from_auth": True},
)
