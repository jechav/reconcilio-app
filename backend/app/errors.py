"""Exception handlers that keep the HTTP error contract the frontend and the
test suite were written against under FastAPI:

- every error body is `{"detail": ...}` with no extra keys;
- request validation failures are 422 (Litestar's default is 400), with
  `detail` a list of `{"loc", "msg", "type"}` items.
"""

from typing import Any

from litestar import Request, Response
from litestar.exceptions import HTTPException, ValidationException
from litestar.status_codes import HTTP_422_UNPROCESSABLE_ENTITY
from litestar.types import ExceptionHandlersMap


def _http_exception_handler(_: Request[Any, Any, Any], exc: HTTPException) -> Response[dict[str, Any]]:
    return Response(
        content={"detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


def _validation_exception_handler(
    _: Request[Any, Any, Any], exc: ValidationException
) -> Response[dict[str, Any]]:
    extra = exc.extra if isinstance(exc.extra, list) else []
    errors = [
        {
            "loc": [part for part in (item.get("source") or "body", item.get("key")) if part],
            "msg": item.get("message", ""),
            "type": "value_error",
        }
        for item in extra
        if isinstance(item, dict)
    ]
    if not errors:
        errors = [{"loc": [], "msg": exc.detail, "type": "value_error"}]
    return Response(content={"detail": errors}, status_code=HTTP_422_UNPROCESSABLE_ENTITY)


exception_handlers: ExceptionHandlersMap = {
    HTTPException: _http_exception_handler,
    ValidationException: _validation_exception_handler,
}
