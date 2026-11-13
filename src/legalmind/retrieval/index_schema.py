"""Azure AI Search index schema definition for Dutch legal documents.

Defines the full index schema including vector search (HNSW), semantic ranking,
and field definitions for legal document chunks.
"""

from __future__ import annotations

import structlog
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

from legalmind.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_NAME = "legalmind-legal-docs"
VECTOR_DIMENSIONS = 3072  # text-embedding-3-large
SEMANTIC_CONFIG_NAME = "legal-semantic-config"
VECTOR_PROFILE_NAME = "legal-vector-profile"
HNSW_CONFIG_NAME = "legal-hnsw-config"


# ---------------------------------------------------------------------------
# Index field definitions
# ---------------------------------------------------------------------------


def _build_fields() -> list[SearchField]:
    """Build the full set of fields for the legal documents index.

    Returns:
        List of SearchField definitions covering identifiers, content,
        vector embeddings, metadata, and enrichment fields.
    """
    return [
        # ── Primary identifiers ───────────────────────────────────────
        SimpleField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="document_id",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="chunk_index",
            type=SearchFieldDataType.Int32,
            filterable=True,
            sortable=True,
        ),
        # ── Content and vector ────────────────────────────────────────
        SearchableField(
            name="content",
            type=SearchFieldDataType.String,
            analyzer_name="nl.microsoft",
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=VECTOR_DIMENSIONS,
            vector_search_profile_name=VECTOR_PROFILE_NAME,
        ),
        # ── Document metadata ─────────────────────────────────────────
        SearchableField(
            name="title",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="document_type",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            sortable=True,
        ),
        SimpleField(
            name="language",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="page_number",
            type=SearchFieldDataType.Int32,
            filterable=True,
            sortable=True,
        ),
        SearchableField(
            name="section_title",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        # ── Enrichment fields ─────────────────────────────────────────
        SearchableField(
            name="entities",
            collection=True,  # SearchableField ignores `type=`; this is what makes it Collection(Edm.String)
            filterable=True,
        ),
        SearchableField(
            name="key_phrases",
            collection=True,  # SearchableField ignores `type=`; this is what makes it Collection(Edm.String)
            filterable=True,
        ),
        SearchableField(
            name="summary",
            type=SearchFieldDataType.String,
        ),
        # ── Hierarchy and provenance ──────────────────────────────────
        SimpleField(
            name="parent_id",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="source_url",
            type=SearchFieldDataType.String,
            filterable=False,
        ),
        SimpleField(
            name="created_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Vector search configuration
# ---------------------------------------------------------------------------


def _build_vector_search() -> VectorSearch:
    """Build the HNSW-based vector search configuration.

    Returns:
        VectorSearch with an HNSW algorithm profile tuned for legal document
        retrieval (high recall, moderate latency).
    """
    return VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name=HNSW_CONFIG_NAME,
                parameters=HnswParameters(
                    m=4,
                    ef_construction=400,
                    ef_search=500,
                    metric="cosine",
                ),
            ),
        ],
        profiles=[
            VectorSearchProfile(
                name=VECTOR_PROFILE_NAME,
                algorithm_configuration_name=HNSW_CONFIG_NAME,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Semantic search configuration
# ---------------------------------------------------------------------------


def _build_semantic_search() -> SemanticSearch:
    """Build the semantic ranking configuration for legal content.

    Maps the *title*, *content*, and *key_phrases* fields into the semantic
    configuration so the cross-encoder re-ranker understands the document
    structure.

    Returns:
        SemanticSearch with a single configuration named
        ``legal-semantic-config``.
    """
    return SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG_NAME,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[
                        SemanticField(field_name="content"),
                    ],
                    keywords_fields=[
                        SemanticField(field_name="key_phrases"),
                    ],
                ),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_legal_documents_index(*, index_name: str | None = None) -> SearchIndex:
    """Create the full Azure AI Search index definition for legal documents.

    This builds the ``SearchIndex`` object with all field definitions, vector
    search (HNSW) configuration, and semantic ranking but does **not** send it
    to Azure.  Use :pymethod:`SearchService.create_or_update_index` to apply.

    Args:
        index_name: Override the default index name.  When ``None`` the name
            is read from application settings
            (``AZURE_SEARCH_INDEX_NAME`` / ``legalmind-legal-docs``).

    Returns:
        A fully configured ``SearchIndex`` ready to be created or updated.

    Example::

        from legalmind.services import SearchService
        from legalmind.retrieval import create_legal_documents_index

        index = create_legal_documents_index()
        search_service = SearchService()
        search_service.create_or_update_index(index)
    """
    settings = get_settings()
    resolved_name = index_name or settings.search.index_name

    logger.info(
        "building_index_schema",
        index_name=resolved_name,
        vector_dimensions=VECTOR_DIMENSIONS,
        semantic_config=SEMANTIC_CONFIG_NAME,
    )

    return SearchIndex(
        name=resolved_name,
        fields=_build_fields(),
        vector_search=_build_vector_search(),
        semantic_search=_build_semantic_search(),
    )
