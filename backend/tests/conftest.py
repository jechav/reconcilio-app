import os
import uuid

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://reconcilio:reconcilio@localhost:5432/reconcilio_test",
)
os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-bytes-long-value")

import pytest
from litestar.di import Provide
from litestar.testing import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.config import get_settings
from app.database import Base
from app.main import create_app

settings = get_settings()
engine = create_engine(settings.database_url)
TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Run Celery tasks synchronously in-process during tests -- no worker needed.
celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def db_session():
    session = TestSessionLocal()
    # The app's `db` provider closes its Session after every request, but
    # tests share this one Session with the app and keep reading through it
    # afterwards -- so closing is deferred to teardown.
    close = session.close
    session.close = lambda: None
    try:
        yield session
    finally:
        close()


@pytest.fixture
def make_client(db_session):
    """Build a TestClient whose app uses the test's Session. Keyword
    arguments replace app-level dependencies by key, e.g.
    `make_client(chat_deps=lambda: fake_deps)`."""
    clients = []

    def _make(**dependency_overrides):
        app = create_app(
            session_factory=lambda: db_session,
            dependency_overrides={
                key: Provide(provider, sync_to_thread=False)
                for key, provider in dependency_overrides.items()
            },
        )
        test_client = TestClient(app)
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield _make
    for test_client in clients:
        test_client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def unique_email():
    return f"user-{uuid.uuid4().hex[:8]}@example.com"
