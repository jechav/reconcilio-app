from collections.abc import Callable, Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def session_provider(
    session_factory: Callable[[], Session],
) -> Callable[[], Generator[Session, None, None]]:
    """Build the Litestar `db` dependency: one Session per request, closed
    when the response is done. `create_app` takes the factory so tests can
    hand the app their own Session."""

    def provide_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    return provide_db
