"""Retrieval architecture for Dutch legal document search.

Provides hybrid search (vector + keyword), semantic ranking, LLM-based query
rewriting, confidence scoring, and context assembly with citations over
Azure AI Search.
"""

from legalmind.retrieval.confidence import (
    ConfidenceLevel,
    ConfidenceScore,
    RetrievalConfidenceScorer,
)
from legalmind.retrieval.context_builder import ContextBuilder, ContextResult
from legalmind.retrieval.hybrid_search import HybridSearchEngine, SearchResult
from legalmind.retrieval.index_schema import create_legal_documents_index
from legalmind.retrieval.query_rewriter import QueryRewriter
from legalmind.retrieval.semantic_ranker import SemanticRankerConfig

__all__ = [
    "ConfidenceLevel",
    "ConfidenceScore",
    "ContextBuilder",
    "ContextResult",
    "HybridSearchEngine",
    "QueryRewriter",
    "RetrievalConfidenceScorer",
    "SearchResult",
    "SemanticRankerConfig",
    "create_legal_documents_index",
]
