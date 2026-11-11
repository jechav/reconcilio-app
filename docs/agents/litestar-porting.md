# Porting a router from FastAPI to Litestar

How to port one file in `backend/app/routers/`. The decision and its limits are in `docs/adr/0004-litestar-replaces-fastapi.md`. Reference ports: `routers/orgs.py` (authenticated, role guards, bodies), `routers/auth.py` (public), `routers/health.py` (custom status code).

## Rules

- **Like-for-like.** Do not change behaviour, paths, status codes, response fields or error messages. Do not introduce async handlers, DTOs, controllers or msgspec models.
- **The router's existing tests are the contract.** They must pass without being edited. The only allowed test edit is replacing `app.dependency_overrides` (see "Test overrides").
- **No deprecated Litestar APIs.** pytest fails on `LitestarDeprecationWarning`, so every handler parameter must say where it comes from.
- Rewrite the file in place. When you are done it must not import `fastapi`, `app.deps` or `app.database.get_db`.

## Translation table

| FastAPI | Litestar |
|---|---|
| `router = APIRouter(prefix="/x", tags=["x"])` at the top | `router = Router(path="/x", route_handlers=[...], tags=["x"])` at the **bottom**, listing every handler |
| `@router.get("/p", response_model=Out)` | `@get("/p", sync_to_thread=True)` and return annotation `-> Out` |
| `@router.post("", ...)` (router root) | `@post("/", ...)` |
| handler returns an ORM object | return `Out.model_validate(obj)`; for lists, `[Out.model_validate(o) for o in rows]` |
| `payload: Model` (request body) | `data: JSONBody[Model]` — the parameter **must** be named `data` |
| `db: Session = Depends(get_db)` | `db: NamedDependency[Session]` |
| `principal: Principal = Depends(get_current_principal)` | `principal: NamedDependency[Principal]` |
| `Depends(require_role(OrgRole.owner))` | `guards=[require_role(OrgRole.owner)]` on the decorator, plus `principal: NamedDependency[Principal]` if the handler uses it |
| `item_id: uuid.UUID` with `"/{item_id}"` | `item_id: FromPath[uuid.UUID]` with `"/{item_id:uuid}"` |
| `q: str = Query(...)`, or a bare query argument | `q: FromQuery[str]`; optional: `q: FromQuery[str | None] = None`; with constraints: `Annotated[int, QueryParameter(ge=1)]` |
| `raise HTTPException(status.HTTP_404_NOT_FOUND, "msg")` | `raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="msg")` from `litestar.exceptions` |
| `status.HTTP_...` | constants from `litestar.status_codes` |
| `fastapi.responses.Response(content=..., media_type=...)` | `litestar.Response(content=..., media_type=...)`, return annotation `Response[bytes]` or `Response[str]` |

Imports: `from litestar import Router, get, post, patch, put, delete`, `from litestar.di import NamedDependency`, `from litestar.params import FromPath, FromQuery, JSONBody`, `from app.auth import Principal, require_role`.

## Things that differ silently

- **Status codes.** Litestar defaults: `POST` 201, `DELETE` 204, everything else 200. FastAPI defaulted to 200 for all. Check every handler's old `status_code=` and set it explicitly wherever the Litestar default differs — a `POST` that returned 200 needs `status_code=HTTP_200_OK`; a `DELETE` that returned a body needs `status_code=HTTP_200_OK`.
- **Sync handlers.** Every handler that touches the database or does I/O gets `sync_to_thread=True`. Omitting it raises a warning and blocks the event loop.
- **Auth is on by default.** The JWT middleware protects every route. Only a public router sets `opt={"exclude_from_auth": True}`.
- **Role checks happen in guards, before the handler runs**, and raise 403 `Insufficient role`. Do not re-check the role inside the handler.
- **Validation failures are 422** with `detail` a list, via `app/errors.py`. If a handler raised `HTTPException(422, "message")` by hand, keep doing that — `detail` stays a string.
- **A malformed path parameter is 404, not 422.** `/{item_id:uuid}` does not match a non-UUID segment, so the request never reaches validation. This is an accepted difference; do not work around it.
- **Dependencies beyond `db` and `principal`** are declared on the Router: `dependencies={"chat_deps": Provide(get_chat_deps, sync_to_thread=False)}`, and used as `chat_deps: NamedDependency[ChatDeps]`.

## Registering

Add the module to the import and to `route_handlers` in `backend/app/main.py`, keeping the existing order.

## Test overrides

Litestar has no `app.dependency_overrides`. A test that needs a fake dependency builds its own client with the `make_client` fixture from `tests/conftest.py`:

```python
def test_something(make_client):
    client = make_client(chat_deps=lambda: fake_deps)
```

The keyword is the dependency key declared on the Router.

## Done when

From `backend/`, with Postgres and MinIO reachable as in CI:

```bash
uv run pytest tests/test_<router>.py tests/test_http_contract.py
```

```bash
uv run mypy app
```

Both pass, the router's test file is unmodified (apart from the override change above), and the file has no `fastapi` import. Tests for routers that are not ported yet fail with 404 — ignore them.
