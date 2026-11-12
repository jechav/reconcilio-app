from collections.abc import Callable, Mapping
from typing import Any

from litestar import Litestar
from litestar.config.cors import CORSConfig
from litestar.di import Provide
from litestar.middleware import DefineMiddleware
from sqlalchemy.orm import Session

from app.auth import JWTAuthMiddleware, provide_principal
from app.config import get_settings
from app.database import SessionLocal, session_provider
from app.errors import exception_handlers
from app.logging_config import configure_logging
from app.chat.agent import get_chat_deps
from app.routers import (
    audit,
    auth,
    categories,
    chat,
    dashboard,
    documents,
    export,
    health,
    orgs,
    reconciliation,
    transactions,
)


def create_app(
    session_factory: Callable[[], Session] = SessionLocal,
    dependency_overrides: Mapping[str, Provide] | None = None,
) -> Litestar:
    """Build the API. Tests pass their own `session_factory`, and replace any
    other app-level dependency by key through `dependency_overrides` --
    Litestar has no equivalent of FastAPI's `app.dependency_overrides`."""
    settings = get_settings()
    dependencies: dict[str, Any] = {
        "db": Provide(session_provider(session_factory)),
        "principal": Provide(provide_principal, sync_to_thread=False),
        # Declared here, not on the chat Router, so tests can replace it
        # through `dependency_overrides` (a Router-level key would shadow it).
        "chat_deps": Provide(get_chat_deps, sync_to_thread=False),
        **(dependency_overrides or {}),
    }

    return Litestar(
        route_handlers=[
            health.router,
            auth.router,
            orgs.router,
            documents.router,
            reconciliation.router,
            categories.router,
            transactions.router,
            dashboard.router,
            export.router,
            audit.router,
            chat.router,
        ],
        dependencies=dependencies,
        middleware=[
            # /schema is Litestar's OpenAPI UI; public routers opt out with
            # opt={"exclude_from_auth": True}. OPTIONS is skipped so CORS
            # preflights never need a token.
            DefineMiddleware(JWTAuthMiddleware, exclude=["^/schema"], exclude_http_methods=["OPTIONS"]),
        ],
        cors_config=CORSConfig(
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        exception_handlers=exception_handlers,
    )


configure_logging()

app = create_app()
