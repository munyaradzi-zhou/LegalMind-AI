"""Entra ID sign-in: validates the bearer token and sets the request's principal.

With sign-in required (AUTH_ENABLED=true, or APP_ENV=production) every non-public path
needs an access token for the API's app registration. The principal comes from its claims:

- ``oid``: the user (or, for app-only tokens, the calling application)
- ``roles``: app roles (Lawyer, Paralegal, Admin)
- ``groups``: security group object IDs; they decide which matters' files the user sees

When a user is in too many groups Entra ID leaves ``groups`` out of the token (the
"overage" claim). The user then gets public documents only, rather than a Graph lookup
on every request; assign the matter groups to the app registration to keep tokens small.

Without sign-in (local development, the evaluation gate) the caller is an unrestricted
developer, unless the request describes a test user with ``X-Dev-User``, ``X-Dev-Roles``
and ``X-Dev-Matters`` (comma-separated). The trimming tests and the access evaluation
use those headers; they are ignored when sign-in is required.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import jwt
import structlog
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from legalmind.access import Principal, matter_group, set_principal
from legalmind.config import get_settings

if TYPE_CHECKING:
    from starlette.responses import Response

logger = structlog.get_logger(__name__)

# Paths that don't require authentication
PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class EntraIDAuthMiddleware(BaseHTTPMiddleware):
    """Sets the request's principal from its Entra ID token, or answers 401."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()

        if request.url.path in PUBLIC_PATHS:
            set_principal(None)
            return await call_next(request)

        if not settings.requires_sign_in:
            principal = dev_principal(request.headers)
        else:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                # Return the response directly: an HTTPException raised inside
                # BaseHTTPMiddleware is not handled by FastAPI and becomes a 500.
                return _unauthorized("Missing or invalid Authorization header")
            try:
                claims = await asyncio.to_thread(validate_token, auth_header[7:])
            except Exception as e:
                logger.warning("auth_failed", error=str(e), path=request.url.path)
                return _unauthorized("Invalid or expired token")
            principal = principal_from_claims(claims)
            logger.info("auth_success", user_id=principal.user_id, roles=sorted(principal.roles), path=request.url.path)

        # The contextvar is copied into the task that runs the route, so the search
        # engine sees this principal however deep the call is made.
        set_principal(principal)
        request.state.principal = principal
        request.state.user_id = principal.user_id
        return await call_next(request)


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def dev_principal(headers: Any) -> Principal:
    """The caller when sign-in is off: unrestricted, or the test user the headers describe."""
    user = headers.get("X-Dev-User")
    if not user:
        return Principal.everything()
    return Principal(
        user_id=user,
        name=user,
        roles=frozenset(_csv(headers.get("X-Dev-Roles"))),
        groups=frozenset(matter_group(m) for m in _csv(headers.get("X-Dev-Matters"))),
    )


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    """The principal described by a validated token."""
    if "groups" in claims.get("_claim_names", {}):
        logger.warning("auth_groups_overage", user_id=claims.get("oid"))
    return Principal(
        user_id=str(claims.get("oid") or claims.get("sub") or "unknown"),
        name=str(claims.get("preferred_username") or claims.get("name") or claims.get("azp") or ""),
        roles=frozenset(claims.get("roles") or []),
        groups=frozenset(claims.get("groups") or []),
    )


@lru_cache(maxsize=1)
def _jwks_client(tenant_id: str) -> jwt.PyJWKClient:
    # Caches the signing keys (and refetches on an unknown key ID), instead of
    # downloading them on every request.
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", lifespan=3600)


def validate_token(token: str) -> dict[str, Any]:
    """Check the token's signature, issuer, audience and expiry; return its claims.

    Accepts v2.0 tokens (audience = the app's client ID) and v1.0 tokens (audience = its
    Application ID URI), since which one a client gets depends on how it asks.
    """
    settings = get_settings()
    tenant = settings.azure_tenant_id
    if not tenant:
        raise ValueError("AZURE_TENANT_ID is not set")
    audiences = _csv(settings.azure_api_audience) or [f"api://{tenant}"]
    key = _jwks_client(tenant).get_signing_key_from_jwt(token)
    claims: dict[str, Any] = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=audiences,
        issuer=[f"https://login.microsoftonline.com/{tenant}/v2.0", f"https://sts.windows.net/{tenant}/"],
        options={"require": ["exp", "iss", "aud"]},
    )
    if claims.get("tid") not in (None, tenant):
        raise ValueError("Token from another tenant")
    return claims


def get_principal(request: Request) -> Principal:
    """Route dependency: the signed-in caller (set by EntraIDAuthMiddleware)."""
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return principal


def require_admin(request: Request) -> Principal:
    """Route dependency: the caller must have the Admin app role."""
    principal = get_principal(request)
    if not principal.can_ingest:
        raise HTTPException(status_code=403, detail="Only the Admin role can add documents")
    return principal
