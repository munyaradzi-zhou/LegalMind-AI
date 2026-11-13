"""Azure Entra ID (Azure AD) JWT authentication middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from legalmind.config import get_settings

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from fastapi import Request
    from starlette.responses import Response

logger = structlog.get_logger(__name__)
security = HTTPBearer(auto_error=False)

# Paths that don't require authentication
PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class EntraIDAuthMiddleware(BaseHTTPMiddleware):
    """Validates Azure Entra ID JWT tokens on incoming requests.

    In development mode, authentication is optional.
    In production, all non-public endpoints require a valid token.

    The middleware extracts:
    - user_id from the 'oid' claim
    - user_email from the 'preferred_username' claim
    - roles from the 'roles' claim
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()

        # Skip auth for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth in development
        if not settings.is_production:
            request.state.user_id = "dev-user"
            request.state.user_email = "dev@legalmind.local"
            request.state.user_roles = ["admin"]
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # Return the response directly: an HTTPException raised inside
            # BaseHTTPMiddleware is not handled by FastAPI and becomes a 500.
            return JSONResponse({"detail": "Missing or invalid Authorization header"}, status_code=401)

        token = auth_header[7:]  # Strip "Bearer "

        try:
            audience = settings.azure_api_audience or f"api://{settings.azure_tenant_id}"
            claims = await self._validate_token(token, settings.azure_tenant_id, audience)
            request.state.user_id = claims.get("oid", "unknown")
            request.state.user_email = claims.get("preferred_username", "unknown")
            request.state.user_roles = claims.get("roles", [])

            logger.info("auth_success", user_id=request.state.user_id, path=request.url.path)
        except Exception as e:
            logger.warning("auth_failed", error=str(e), path=request.url.path)
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)

        return await call_next(request)

    @staticmethod
    async def _validate_token(token: str, tenant_id: str, audience: str) -> dict[str, Any]:
        """Validate a JWT token against Azure Entra ID.

        Uses the JWKS endpoint to verify the token signature,
        then validates issuer, audience, and expiration.

        Args:
            token: The raw JWT string.
            tenant_id: The Azure AD tenant ID.
            audience: The expected "aud" claim.

        Returns:
            The validated token claims.
        """
        import httpx
        import jwt

        # Fetch JWKS from Azure AD
        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url)
            jwks = response.json()

        # Decode and validate the token
        header = jwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k["kid"] == header["kid"]), None)

        if not key:
            raise ValueError("Token signing key not found in JWKS")

        public_key = cast("RSAPublicKey", jwt.algorithms.RSAAlgorithm.from_jwk(key))

        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        )

        return claims
