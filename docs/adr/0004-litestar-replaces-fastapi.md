---
status: accepted
---

# Litestar replaces FastAPI, as a like-for-like port

The backend's HTTP layer moves from FastAPI to Litestar 2.x. The reasons are Litestar's layered dependency injection and guards — role checks become declarative guards on a route instead of per-route dependency factories, and auth runs once in a middleware — and wanting to learn the framework. Performance was not a reason: request latency here is dominated by Postgres, Textract and LLM calls.

The port is deliberately like-for-like. Handlers stay synchronous with a sync SQLAlchemy `Session` (run in a thread with `sync_to_thread=True`), request and response shapes stay the Pydantic models in `app/schemas.py`, and handlers stay plain functions grouped in `Router`s. Async SQLAlchemy, Advanced Alchemy, msgspec models, DTOs and class-based controllers were each considered and left out, so a failing test can only be blamed on the framework swap. Any of them can follow as its own change.

The HTTP contract is frozen: same paths, same status codes and the same `{"detail": ...}` error bodies, so the frontend does not change. Litestar's defaults differ in two places — validation failures are 400 and error bodies carry extra keys — so `app/errors.py` pins 422 and the old body shape, and `tests/test_http_contract.py` guards it. The one accepted difference is that the interactive API docs move from `/docs` to `/schema`.

Code is written in Litestar's explicit, 3.0-ready style (`FromPath`, `FromQuery`, `JSONBody`, `NamedDependency`), and pytest turns `LitestarDeprecationWarning` into an error, so moving to Litestar 3 should not need a second pass over every handler.

Litestar has no `app.dependency_overrides`. The app is built by `create_app(session_factory=..., dependency_overrides=...)`, which is what tests use to inject their Session and fakes.

The conventions for porting or adding a router are in `docs/agents/litestar-porting.md`.
