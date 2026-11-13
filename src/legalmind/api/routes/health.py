"""Health check endpoints for monitoring and readiness probes."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter

from legalmind import __version__
from legalmind.api.models.responses import HealthResponse
from legalmind.config import get_settings

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Basic health check — always returns 200 if the app is running.

    Used by Azure Container Apps for liveness probes.
    """
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        version=__version__,
        environment=settings.app_env,
        timestamp=datetime.now(UTC),
        services={},
    )


@router.get("/health/ready", response_model=HealthResponse)
async def readiness_check() -> HealthResponse:
    """Readiness check — verifies dependent Azure services are reachable.

    Used by Azure Container Apps for readiness probes.
    """
    settings = get_settings()
    services: dict[str, str] = {}

    # Check Azure OpenAI
    try:
        from legalmind.services.azure_openai import AzureOpenAIService

        AzureOpenAIService()
        services["azure_openai"] = "reachable"
    except Exception as e:
        services["azure_openai"] = f"error: {e}"

    # Check Azure AI Search
    try:
        from legalmind.services.search_client import SearchService

        SearchService()
        services["azure_search"] = "reachable"
    except Exception as e:
        services["azure_search"] = f"error: {e}"

    overall_status = "ready" if all("error" not in v for v in services.values()) else "degraded"

    return HealthResponse(
        status=overall_status,
        version=__version__,
        environment=settings.app_env,
        timestamp=datetime.now(UTC),
        services=services,
    )
