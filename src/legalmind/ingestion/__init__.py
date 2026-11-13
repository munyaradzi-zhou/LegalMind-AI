"""Document ingestion pipeline for Dutch legal documents.

Orchestrates the full ingestion flow: upload, analysis, chunking,
enrichment, embedding generation, and indexing into Azure AI Search.
"""

from legalmind.ingestion.chunking import fixed_size_chunk, parent_child_chunk, semantic_chunk
from legalmind.ingestion.content_understanding import ContentUnderstandingClient
from legalmind.ingestion.document_intelligence import DocumentIntelligenceAnalyzer
from legalmind.ingestion.embedding import EmbeddingGenerator
from legalmind.ingestion.enrichment import ChunkEnricher
from legalmind.ingestion.indexing import SearchIndexer
from legalmind.ingestion.pipeline import IngestionPipeline, IngestionResult

__all__ = [
    "ChunkEnricher",
    "ContentUnderstandingClient",
    "DocumentIntelligenceAnalyzer",
    "EmbeddingGenerator",
    "IngestionPipeline",
    "IngestionResult",
    "SearchIndexer",
    "fixed_size_chunk",
    "parent_child_chunk",
    "semantic_chunk",
]
