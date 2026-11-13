"""The search index schema must match the documents the indexer uploads."""

from __future__ import annotations

from legalmind.retrieval.index_schema import VECTOR_DIMENSIONS, create_legal_documents_index


def test_list_fields_are_collections() -> None:
    fields = {f.name: f for f in create_legal_documents_index(index_name="test").fields}
    # The indexer sends lists for these; a plain Edm.String field rejects the whole batch.
    assert fields["entities"].type == "Collection(Edm.String)"
    assert fields["key_phrases"].type == "Collection(Edm.String)"


def test_vector_field_matches_embedding_size() -> None:
    fields = {f.name: f for f in create_legal_documents_index(index_name="test").fields}
    assert fields["content_vector"].type == "Collection(Edm.Single)"
    assert fields["content_vector"].vector_search_dimensions == VECTOR_DIMENSIONS == 3072
