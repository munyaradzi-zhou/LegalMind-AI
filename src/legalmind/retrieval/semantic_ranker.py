"""Semantic ranking configuration for the legal documents search index.

Provides helpers to define, apply, and update the Azure AI Search semantic
ranking profiles used by the LegalMind retrieval pipeline.  Semantic ranking
uses a Microsoft-trained cross-encoder model to re-rank keyword and hybrid
search results based on deep understanding of query-document relevance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from azure.search.documents.indexes.models import (
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
)

from legalmind.services import SearchService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticFieldMapping:
    """Describes how index fields map to semantic roles.

    Attributes:
        title_field: The field containing the document or chunk title.
        content_fields: Fields that hold the main searchable body text.
        keyword_fields: Fields with key phrases, tags, or entity lists.
    """

    title_field: str = "title"
    content_fields: list[str] = field(default_factory=lambda: ["content"])
    keyword_fields: list[str] = field(default_factory=lambda: ["key_phrases"])


@dataclass(frozen=True, slots=True)
class SemanticProfile:
    """A named semantic ranking profile that can be registered on an index.

    Attributes:
        name: Configuration name referenced at query time.
        mapping: Field mapping for the profile.
    """

    name: str = "legal-semantic-config"
    mapping: SemanticFieldMapping = field(default_factory=SemanticFieldMapping)


# ---------------------------------------------------------------------------
# Predefined profiles
# ---------------------------------------------------------------------------

#: Default profile optimised for Dutch legal documents.
LEGAL_DEFAULT_PROFILE = SemanticProfile(
    name="legal-semantic-config",
    mapping=SemanticFieldMapping(
        title_field="title",
        content_fields=["content"],
        keyword_fields=["key_phrases"],
    ),
)

#: Extended profile that also considers the summary field.
LEGAL_EXTENDED_PROFILE = SemanticProfile(
    name="legal-semantic-extended",
    mapping=SemanticFieldMapping(
        title_field="title",
        content_fields=["content", "summary"],
        keyword_fields=["key_phrases", "entities"],
    ),
)


# ---------------------------------------------------------------------------
# Configuration builder
# ---------------------------------------------------------------------------


class SemanticRankerConfig:
    """Builds and applies semantic ranking configurations on an Azure AI Search index.

    Usage::

        config = SemanticRankerConfig()
        config.add_profile(LEGAL_DEFAULT_PROFILE)
        config.add_profile(LEGAL_EXTENDED_PROFILE)

        # Apply to an existing SearchIndex object
        index = config.apply(index)

        # Or push directly to Azure
        await config.enable_on_index("legalmind-legal-docs")
    """

    def __init__(self, *, search_service: SearchService | None = None) -> None:
        self._search = search_service or SearchService()
        self._profiles: list[SemanticProfile] = []

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def add_profile(self, profile: SemanticProfile) -> SemanticRankerConfig:
        """Register a semantic profile.

        Args:
            profile: The ``SemanticProfile`` to add.

        Returns:
            ``self`` for fluent chaining.
        """
        self._profiles.append(profile)
        logger.debug("semantic_profile_added", name=profile.name)
        return self

    def add_default_profiles(self) -> SemanticRankerConfig:
        """Register both pre-defined legal profiles.

        Returns:
            ``self`` for fluent chaining.
        """
        self.add_profile(LEGAL_DEFAULT_PROFILE)
        self.add_profile(LEGAL_EXTENDED_PROFILE)
        return self

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def _build_configuration(self, profile: SemanticProfile) -> SemanticConfiguration:
        """Convert a ``SemanticProfile`` into the SDK's ``SemanticConfiguration``.

        Args:
            profile: The profile to convert.

        Returns:
            A ``SemanticConfiguration`` instance.
        """
        return SemanticConfiguration(
            name=profile.name,
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name=profile.mapping.title_field),
                content_fields=[SemanticField(field_name=f) for f in profile.mapping.content_fields],
                keywords_fields=[SemanticField(field_name=f) for f in profile.mapping.keyword_fields],
            ),
        )

    def build_semantic_search(self) -> SemanticSearch:
        """Build a ``SemanticSearch`` object from all registered profiles.

        Returns:
            A ``SemanticSearch`` containing every profile added via
            ``add_profile``.

        Raises:
            ValueError: If no profiles have been registered.
        """
        if not self._profiles:
            raise ValueError("No semantic profiles registered.  Call add_profile() or add_default_profiles() first.")

        configurations = [self._build_configuration(p) for p in self._profiles]

        logger.info(
            "semantic_search_built",
            profile_count=len(configurations),
            names=[c.name for c in configurations],
        )

        return SemanticSearch(configurations=configurations)

    # ------------------------------------------------------------------
    # Applying to an index
    # ------------------------------------------------------------------

    def apply(self, index: SearchIndex) -> SearchIndex:
        """Apply the registered semantic configurations to a ``SearchIndex``.

        Replaces any existing ``semantic_search`` property on the index with
        the configurations built from registered profiles.

        Args:
            index: The ``SearchIndex`` to modify.

        Returns:
            The same ``SearchIndex`` instance (mutated in place) for
            convenience.
        """
        index.semantic_search = self.build_semantic_search()
        logger.info("semantic_config_applied", index_name=index.name)
        return index

    async def enable_on_index(self, index_name: str) -> None:
        """Fetch an existing index from Azure, apply semantic config, and push.

        This is a convenience method that performs a full read-modify-write
        cycle against the Azure AI Search management plane.

        Args:
            index_name: The name of the index to update.

        Raises:
            ValueError: If no profiles have been registered.
        """
        logger.info("enabling_semantic_ranking", index_name=index_name)

        client = self._search._get_index_client()
        index = client.get_index(index_name)

        self.apply(index)

        client.create_or_update_index(index)

        logger.info(
            "semantic_ranking_enabled",
            index_name=index_name,
            profiles=[p.name for p in self._profiles],
        )
