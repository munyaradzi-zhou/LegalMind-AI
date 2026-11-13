"""Simple in-memory rate limiting middleware.

In production, rate limiting is handled by Azure API Management (APIM).
This provides a lightweight fallback for development and direct access.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

import structlog
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

logger = structlog.get_logger(__name__)

# Default: 60 requests per minute per user
DEFAULT_RATE_LIMIT = 60
DEFAULT_WINDOW_SECONDS = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token bucket rate limiter per user/IP.

    Tracks request counts per user within a sliding time window.
    Returns 429 Too Many Requests when the limit is exceeded.
    """

    def __init__(
        self, app: ASGIApp, *, rate_limit: int = DEFAULT_RATE_LIMIT, window_seconds: int = DEFAULT_WINDOW_SECONDS
    ) -> None:
        super().__init__(app)
        self._rate_limit = rate_limit
        self._window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip rate limiting for health checks
        if request.url.path.startswith("/health"):
            return await call_next(request)

        # Identify the client
        client_id = getattr(request.state, "user_id", None) or (request.client.host if request.client else "unknown")
        now = time.time()

        # Clean old entries outside the window
        self._requests[client_id] = [t for t in self._requests[client_id] if now - t < self._window_seconds]

        # Check rate limit
        if len(self._requests[client_id]) >= self._rate_limit:
            logger.warning("rate_limit_exceeded", client_id=client_id, limit=self._rate_limit)
            # Returned, not raised: see the note in auth.py.
            return JSONResponse(
                {
                    "detail": (
                        f"Rate limit exceeded. Maximum {self._rate_limit} requests per {self._window_seconds} seconds."
                    )
                },
                status_code=429,
                headers={"Retry-After": str(self._window_seconds)},
            )

        # Record this request
        self._requests[client_id].append(now)

        response = await call_next(request)

        # Add rate limit headers
        remaining = self._rate_limit - len(self._requests[client_id])
        response.headers["X-RateLimit-Limit"] = str(self._rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
