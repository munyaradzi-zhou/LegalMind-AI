"""FastAPI application entrypoint for LegalMind AI."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from legalmind import __version__
from legalmind.api.middleware.auth import EntraIDAuthMiddleware
from legalmind.api.middleware.logging import ObservabilityMiddleware
from legalmind.api.middleware.rate_limiting import RateLimitMiddleware
from legalmind.api.routes import agents, chat, health, ingest, search
from legalmind.config import get_async_azure_credential, get_settings
from legalmind.services.cosmos_client import get_cosmos_service

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger(__name__)


def configure_logging() -> None:
    """Configure structured logging with structlog."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def configure_opentelemetry() -> None:
    """Configure OpenTelemetry with Azure Monitor exporter.

    Sends traces, metrics, and logs to Application Insights.
    """
    settings = get_settings()
    connection_string = settings.applicationinsights_connection_string

    if not connection_string:
        logger.warning("opentelemetry_skipped", reason="No Application Insights connection string")
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=connection_string)
        logger.info("opentelemetry_configured")
    except ImportError:
        logger.warning("opentelemetry_skipped", reason="azure-monitor-opentelemetry not installed")


async def ensure_search_index() -> None:
    """Create or update the Azure AI Search index (idempotent) when Search is configured.

    A failure is logged rather than raised, so the API still starts (e.g. while
    role assignments are propagating) and the next start retries.
    """
    if not get_settings().search.endpoint:
        return
    from legalmind.retrieval.index_schema import create_legal_documents_index
    from legalmind.services.search_client import SearchService

    try:
        await asyncio.to_thread(SearchService().create_or_update_index, create_legal_documents_index())
        logger.info("search_index_ready")
    except Exception:
        logger.exception("search_index_setup_failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — startup and shutdown."""
    settings = get_settings()
    logger.info(
        "application_starting",
        version=__version__,
        environment=settings.app_env,
    )

    # Configure OpenTelemetry on startup
    configure_opentelemetry()
    await ensure_search_index()

    yield

    logger.info("application_shutting_down")
    await get_cosmos_service().close()
    await get_async_azure_credential().close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="LegalMind AI",
        description=(
            "Intelligent Legal Case Assistant - AI-powered legal document analysis, retrieval, and agentic workflows"
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ─── Middleware ──────────────────────────────────────────────────────
    # Each add_middleware() wraps everything added before it, so the LAST one
    # added runs FIRST. Request order: CORS → Observability → Auth → RateLimit.
    # Auth runs before rate limiting so limits apply per user; CORS is outermost
    # so even 401/429 responses carry CORS headers the browser can read.
    app.add_middleware(RateLimitMiddleware, rate_limit=60, window_seconds=60)
    app.add_middleware(EntraIDAuthMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── Routes ──────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(chat.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")

    return app


# Configure logging at module load time
configure_logging()

# Create the app instance (used by uvicorn)
app = create_app()


def start() -> None:
    """Start the application server (used by the `legalmind` CLI entrypoint).

    Binds to localhost by default; set API_HOST=0.0.0.0 to listen on all interfaces.
    """
    uvicorn.run(
        "legalmind.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
    )
