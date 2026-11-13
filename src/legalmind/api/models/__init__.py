"""Pydantic request and response models for the API."""

from legalmind.api.models.requests import (
    ChatRequest,
    DocumentIngestRequest,
    SearchRequest,
)
from legalmind.api.models.responses import (
    ChatResponse,
    Citation,
    DocumentIngestResponse,
    HealthResponse,
    SearchResponse,
    SearchResult,
)

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "Citation",
    "DocumentIngestRequest",
    "DocumentIngestResponse",
    "HealthResponse",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
]
