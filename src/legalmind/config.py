"""Centralized configuration management using environment variables and Azure Key Vault."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal, cast

from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shared by every settings class so the nested Azure settings also read the .env file
# (pydantic-settings only reads .env for classes that declare env_file themselves).
_ENV_FILE_CONFIG = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class AzureOpenAISettings(BaseSettings):
    """Azure OpenAI / AI Foundry configuration."""

    model_config = SettingsConfigDict(env_prefix="AZURE_OPENAI_", **_ENV_FILE_CONFIG)

    endpoint: str = ""
    api_version: str = "2024-10-21"
    chat_deployment: str = "gpt-4.1-mini"
    embedding_deployment: str = "text-embedding-3-large"


class AzureSearchSettings(BaseSettings):
    """Azure AI Search configuration."""

    model_config = SettingsConfigDict(env_prefix="AZURE_SEARCH_", **_ENV_FILE_CONFIG)

    endpoint: str = ""
    index_name: str = "legalmind-legal-docs"


class AzureCognitiveSettings(BaseSettings):
    """Azure Cognitive Services (Language, Translator, Document Intelligence, Content Understanding)."""

    model_config = SettingsConfigDict(env_prefix="AZURE_", **_ENV_FILE_CONFIG)

    content_understanding_endpoint: str = ""
    document_intelligence_endpoint: str = ""
    language_endpoint: str = ""
    translator_endpoint: str = "https://api.cognitive.microsofttranslator.com/"
    translator_region: str = "westeurope"


class AzureContentSafetySettings(BaseSettings):
    """Azure AI Content Safety configuration."""

    model_config = SettingsConfigDict(env_prefix="AZURE_CONTENT_SAFETY_", **_ENV_FILE_CONFIG)

    endpoint: str = ""
    severity_threshold: int = 4  # 0-6 scale; 4 = medium and above flagged
    enable_pii_detection: bool = True
    pii_categories_to_redact: list[str] = Field(
        default_factory=lambda: [
            "Person",
            "PhoneNumber",
            "Email",
            "Address",
            "NLBSNNumber",
            "InternationalBankingAccountNumber",
        ]
    )


class AzureCosmosSettings(BaseSettings):
    """Azure Cosmos DB configuration."""

    model_config = SettingsConfigDict(env_prefix="AZURE_COSMOS_", **_ENV_FILE_CONFIG)

    endpoint: str = ""
    database: str = "legalmind"
    container_sessions: str = "sessions"
    container_memory: str = "memory"
    container_audit: str = "audit_log"


class AzureStorageSettings(BaseSettings):
    """Azure Blob Storage configuration."""

    model_config = SettingsConfigDict(env_prefix="AZURE_STORAGE_", **_ENV_FILE_CONFIG)

    account_url: str = ""
    container: str = "legal-documents"


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables."""

    model_config = SettingsConfigDict(**_ENV_FILE_CONFIG)

    # Azure identity
    azure_tenant_id: str = ""
    azure_subscription_id: str = ""
    azure_resource_group: str = "rg-legalmind-dev"
    # Audience ("aud" claim) the API accepts in Entra ID tokens: your app registration's
    # Application ID URI or client ID. Falls back to api://<tenant-id> when empty.
    azure_api_audience: str = ""

    # LLM provider. "openai" = OpenAI API with OPENAI_API_KEY; "azure" = Azure OpenAI with
    # Entra ID (no keys); "auto" = "openai" when OPENAI_API_KEY is set, otherwise "azure".
    llm_provider: Literal["auto", "openai", "azure"] = "auto"
    openai_api_key: SecretStr = SecretStr("")  # SecretStr: never shown in logs or repr()
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-large"  # 3072 dims, matches the search index

    # Key Vault
    azure_keyvault_url: str = ""

    # Application Insights
    applicationinsights_connection_string: str = ""

    # App settings
    app_env: str = "development"
    app_log_level: str = "INFO"
    app_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])

    # Rewrite chat questions with the LLM before searching (QUERY_REWRITING_ENABLED).
    # Off by default: the retrieval evaluation showed no gain for ~1.3 s extra latency.
    query_rewriting_enabled: bool = False

    # Sub-configurations
    openai: AzureOpenAISettings = Field(default_factory=AzureOpenAISettings)
    search: AzureSearchSettings = Field(default_factory=AzureSearchSettings)
    cognitive: AzureCognitiveSettings = Field(default_factory=AzureCognitiveSettings)
    content_safety: AzureContentSafetySettings = Field(default_factory=AzureContentSafetySettings)
    cosmos: AzureCosmosSettings = Field(default_factory=AzureCosmosSettings)
    storage: AzureStorageSettings = Field(default_factory=AzureStorageSettings)

    @field_validator("app_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return cast("list[str]", json.loads(v))
        return cast("list[str]", v)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def uses_openai_api(self) -> bool:
        """True when LLM calls go to the OpenAI API instead of Azure OpenAI."""
        if self.llm_provider == "auto":
            return bool(self.openai_api_key.get_secret_value())
        return self.llm_provider == "openai"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached application settings (singleton)."""
    return Settings()


@lru_cache(maxsize=1)
def get_azure_credential() -> DefaultAzureCredential:
    """Get cached Azure credential (singleton).

    Uses DefaultAzureCredential which supports:
    - Managed Identity (production on Azure)
    - Azure CLI credentials (local development)
    - Environment variables (CI/CD)
    """
    return DefaultAzureCredential()


@lru_cache(maxsize=1)
def get_async_azure_credential() -> AsyncDefaultAzureCredential:
    """Get the cached async Azure credential, for the SDKs' `.aio` clients.

    Async clients must use the async credential: a sync credential would block the
    event loop on every token refresh.
    """
    return AsyncDefaultAzureCredential()


def get_keyvault_secret(secret_name: str) -> str:
    """Retrieve a secret from Azure Key Vault.

    Args:
        secret_name: Name of the secret in Key Vault.

    Returns:
        The secret value as a string.
    """
    settings = get_settings()
    credential = get_azure_credential()
    client = SecretClient(vault_url=settings.azure_keyvault_url, credential=credential)
    return client.get_secret(secret_name).value or ""
