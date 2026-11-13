"""Structured logging middleware with OpenTelemetry and Application Insights integration."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.responses import Response

logger = structlog.get_logger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Request/response logging middleware with correlation IDs.

    Integrates with Azure Application Insights via OpenTelemetry:
    - Assigns a unique correlation ID to each request
    - Logs request method, path, status code, and duration
    - Adds correlation ID to response headers for traceability
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Generate correlation ID
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id

        # Bind correlation ID to structlog context
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        start_time = time.perf_counter()

        logger.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query),
            user_agent=request.headers.get("User-Agent", ""),
        )

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000

            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            # Add correlation ID to response headers
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Response-Time-Ms"] = str(round(duration_ms, 2))

            return response

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "request_failed",
                method=request.method,
                path=request.url.path,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
