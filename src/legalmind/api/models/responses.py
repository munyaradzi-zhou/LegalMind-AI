"""Pydantic response models for the LegalMind AI API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Grounding = Literal["documents", "not_grounded", "unverified", "no_documents"]


class Citation(BaseModel):
    """A source citation from a RAG response."""

    index: int = Field(..., description="Citation number [1], [2], etc.")
    document_id: str = Field(..., description="Source document identifier")
    document_title: str = Field(..., description="Source document title")
    chunk_content: str = Field(..., description="The relevant text excerpt")
    page_number: int | None = Field(None, description="Page number in the source document")
    relevance_score: float = Field(..., description="Relevance score from search")


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""

    message: str = Field(..., description="The assistant's response")
    session_id: str = Field(..., description="Session ID for continuing the conversation")
    agent_name: str = Field(..., description="Which specialist agent handled this request")
    citations: list[Citation] = Field(default_factory=list, description="Source citations")
    token_usage: dict[str, int] = Field(default_factory=dict, description="Token usage breakdown")
    grounding: Grounding = Field(
        default="documents",
        description=(
            "documents: verified as supported by the retrieved documents; "
            "not_grounded: contains claims the documents do not support; "
            "unverified: the grounding check could not run; "
            "no_documents: no document passages were retrieved"
        ),
    )
    groundedness_score: float | None = Field(default=None, description="0-1 score from the grounding check")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message": "Op basis van artikel 12 van het contract...",
                    "session_id": "abc-123",
                    "agent_name": "research_agent",
                    "citations": [
                        {
                            "index": 1,
                            "document_id": "doc-456",
                            "document_title": "Verzekeringsovereenkomst 2024",
                            "chunk_content": "Artikel 12: Opzegging...",
                            "page_number": 5,
                            "relevance_score": 0.95,
                        }
                    ],
                    "token_usage": {
                        "prompt_tokens": 2500,
                        "completion_tokens": 350,
                    },
                }
            ],
        },
    }


class SearchResult(BaseModel):
    """A single search result."""

    document_id: str = Field(..., description="Document identifier")
    chunk_id: str = Field(..., description="Chunk identifier")
    content: str = Field(..., description="Chunk text content")
    title: str = Field(default="", description="Document title")
    document_type: str = Field(default="", description="Document type")
    page_number: int | None = Field(None, description="Page number")
    score: float = Field(..., description="Relevance score")
    reranker_score: float | None = Field(None, description="Semantic reranker score")
    entities: list[str] = Field(default_factory=list, description="Extracted entities")
    key_phrases: list[str] = Field(default_factory=list, description="Key phrases")


class SearchResponse(BaseModel):
    """Response from the search endpoint."""

    results: list[SearchResult] = Field(..., description="Search results")
    total_count: int = Field(..., description="Total number of matching results")
    query_used: str = Field(..., description="The actual query used (may be rewritten)")


class DocumentIngestResponse(BaseModel):
    """Response from the document ingestion endpoint."""

    document_id: str = Field(..., description="Assigned document identifier")
    blob_url: str = Field(..., description="URL of the stored document in Blob Storage")
    chunk_count: int = Field(..., description="Number of chunks created")
    page_count: int = Field(..., description="Number of pages detected")
    entities_found: int = Field(default=0, description="Number of entities extracted")
    status: str = Field(default="completed", description="Ingestion status")


class HealthResponse(BaseModel):
    """Response from the health check endpoint."""

    status: str = Field(..., description="Overall health status")
    version: str = Field(..., description="Application version")
    environment: str = Field(..., description="Current environment (dev/staging/prod)")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    services: dict[str, str] = Field(default_factory=dict, description="Status of dependent services")
