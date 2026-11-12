"""Authentication and authorization for the Litestar app.

`JWTAuthMiddleware` decodes the bearer token once per request and stores the
resulting `Principal` on `request.user`. Handlers receive it through the
app-level `principal` dependency; role checks are guards built by
`require_role`. Public routes opt out with `opt={"exclude_from_auth": True}`
on their Router.
"""

import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from litestar import Request
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult
from litestar.types import Guard

from app.models import OrgRole
from app.security import decode_access_token


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: OrgRole


def _bearer_token(connection: ASGIConnection[Any, Any, Any, Any]) -> str | None:
    scheme, _, token = connection.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class JWTAuthMiddleware(AbstractAuthenticationMiddleware):
    async def authenticate_request(
        self, connection: ASGIConnection[Any, Any, Any, Any]
    ) -> AuthenticationResult:
        token = _bearer_token(connection)
        if token is None:
            raise NotAuthorizedException("Not authenticated")
        try:
            payload = decode_access_token(token)
        except jwt.PyJWTError as exc:
            raise NotAuthorizedException("Invalid or expired token") from exc

        principal = Principal(
            user_id=uuid.UUID(payload["sub"]),
            org_id=uuid.UUID(payload["org_id"]),
            role=OrgRole(payload["role"]),
        )
        return AuthenticationResult(user=principal, auth=token)


def provide_principal(request: Request[Principal, str, Any]) -> Principal:
    return request.user


def require_role(*allowed: OrgRole) -> Guard:
    def _check_role(connection: ASGIConnection[Any, Principal, Any, Any], _: BaseRouteHandler) -> None:
        if connection.user.role not in allowed:
            raise PermissionDeniedException("Insufficient role")

    return _check_role
