"""Azure Content Understanding REST API wrapper for legal document analysis.

Uses the Azure Content Understanding service (preview) to extract structured
content from legal documents including text, tables, and figures via custom
analyzers tailored for Dutch legal document types.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any, cast

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from legalmind.config import get_async_azure_credential, get_settings

logger = structlog.get_logger(__name__)

# Azure Content Understanding API version
_API_VERSION = "2024-12-01-preview"

# Polling interval in seconds when waiting for analysis results
_POLL_INTERVAL_SECONDS = 2.0

# Maximum time to wait for a single analysis operation (seconds)
_ANALYSIS_TIMEOUT_SECONDS = 300


class LegalDocumentType(StrEnum):
    """Legal document types with corresponding Content Understanding analyzer configurations."""

    CONTRACT = "contract"
    COURT_FILING = "court_filing"
    CORRESPONDENCE = "correspondence"
    GENERAL = "general"


# Custom analyzer definitions for each legal document type.
# These are sent to the Content Understanding REST API to configure extraction behavior.
_ANALYZER_DEFINITIONS: dict[LegalDocumentType, dict[str, Any]] = {
    LegalDocumentType.CONTRACT: {
        "analyzerId": "legalmind-contract-analyzer",
        "description": "Analyzer for Dutch legal contracts — extracts parties, clauses, obligations, and signatures.",
        "scenario": "document",
        "fieldSchema": {
            "fields": {
                "parties": {
                    "type": "array",
                    "description": "Contractual parties (names and roles)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Party name"},
                            "role": {"type": "string", "description": "Party role (e.g. opdrachtgever, opdrachtnemer)"},
                        },
                    },
                },
                "effective_date": {"type": "string", "description": "Contract effective date"},
                "expiration_date": {"type": "string", "description": "Contract expiration date"},
                "governing_law": {"type": "string", "description": "Governing law / jurisdiction"},
                "clauses": {
                    "type": "array",
                    "description": "Major contract clauses",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                            "clause_number": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
    LegalDocumentType.COURT_FILING: {
        "analyzerId": "legalmind-court-filing-analyzer",
        "description": "Analyzer for Dutch court filings — extracts case numbers, judges, rulings.",
        "scenario": "document",
        "fieldSchema": {
            "fields": {
                "case_number": {"type": "string", "description": "ECLI or case reference number"},
                "court": {"type": "string", "description": "Court name (e.g. Rechtbank Amsterdam)"},
                "date_filed": {"type": "string", "description": "Filing or ruling date"},
                "judge": {"type": "string", "description": "Presiding judge"},
                "parties_plaintiff": {"type": "string", "description": "Plaintiff / eiser"},
                "parties_defendant": {"type": "string", "description": "Defendant / gedaagde"},
                "ruling_summary": {"type": "string", "description": "Summary of the ruling / beslissing"},
            },
        },
    },
    LegalDocumentType.CORRESPONDENCE: {
        "analyzerId": "legalmind-correspondence-analyzer",
        "description": "Analyzer for legal correspondence — extracts sender, recipient, subject, deadlines.",
        "scenario": "document",
        "fieldSchema": {
            "fields": {
                "sender": {"type": "string", "description": "Sender name and organization"},
                "recipient": {"type": "string", "description": "Recipient name and organization"},
                "date": {"type": "string", "description": "Letter date"},
                "subject": {"type": "string", "description": "Subject / betreft"},
                "reference": {"type": "string", "description": "Reference number / kenmerk"},
                "deadlines": {
                    "type": "array",
                    "description": "Any mentioned deadlines or termijnen",
                    "items": {"type": "string"},
                },
                "action_items": {
                    "type": "array",
                    "description": "Required actions mentioned in the letter",
                    "items": {"type": "string"},
                },
            },
        },
    },
    LegalDocumentType.GENERAL: {
        "analyzerId": "legalmind-general-analyzer",
        "description": "General-purpose legal document analyzer — extracts text, tables, and structural content.",
        "scenario": "document",
        "fieldSchema": {
            "fields": {
                "title": {"type": "string", "description": "Document title"},
                "date": {"type": "string", "description": "Document date"},
                "author": {"type": "string", "description": "Document author or organization"},
                "summary": {"type": "string", "description": "Brief document summary"},
            },
        },
    },
}


class ContentUnderstandingClient:
    """Async client for the Azure Content Understanding REST API.

    Provides methods to create custom analyzers for legal documents and to
    analyze documents using those analyzers. Authentication is handled via
    Azure AD (DefaultAzureCredential).

    Usage::

        client = ContentUnderstandingClient()
        await client.ensure_analyzers()
        result = await client.analyze_document(
            document_bytes=pdf_bytes,
            document_type=LegalDocumentType.CONTRACT,
            content_type="application/pdf",
        )
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._credential = get_async_azure_credential()
        self._endpoint = settings.cognitive.content_understanding_endpoint.rstrip("/")
        self._analyzers_ensured: set[LegalDocumentType] = set()

    async def _get_auth_headers(self) -> dict[str, str]:
        """Obtain a fresh Azure AD bearer token for Cognitive Services."""
        token = await self._credential.get_token("https://cognitiveservices.azure.com/.default")
        return {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }

    # ─── Analyzer Management ─────────────────────────────────────────────

    async def ensure_analyzers(self) -> None:
        """Create or update all legal document analyzers.

        Idempotent — safe to call on every application startup.
        """
        for doc_type, definition in _ANALYZER_DEFINITIONS.items():
            if doc_type not in self._analyzers_ensured:
                await self._create_or_update_analyzer(doc_type, definition)
                self._analyzers_ensured.add(doc_type)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _create_or_update_analyzer(
        self,
        doc_type: LegalDocumentType,
        definition: dict[str, Any],
    ) -> None:
        """Create or update a single analyzer via the REST API."""
        analyzer_id = definition["analyzerId"]
        url = f"{self._endpoint}/contentunderstanding/analyzers/{analyzer_id}?api-version={_API_VERSION}"
        headers = await self._get_auth_headers()

        logger.info("ensuring_analyzer", analyzer_id=analyzer_id, doc_type=doc_type.value)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(url, headers=headers, json=definition)
            # 200 (updated) or 201 (created) are both acceptable
            if response.status_code not in (200, 201):
                response.raise_for_status()

        logger.info("analyzer_ensured", analyzer_id=analyzer_id, status=response.status_code)

    async def get_analyzer(self, doc_type: LegalDocumentType) -> dict[str, Any] | None:
        """Retrieve an analyzer definition from the service.

        Args:
            doc_type: The legal document type whose analyzer to retrieve.

        Returns:
            The analyzer definition dict, or None if not found.
        """
        definition = _ANALYZER_DEFINITIONS.get(doc_type)
        if definition is None:
            return None

        analyzer_id = definition["analyzerId"]
        url = f"{self._endpoint}/contentunderstanding/analyzers/{analyzer_id}?api-version={_API_VERSION}"
        headers = await self._get_auth_headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return cast("dict[str, Any]", response.json())

    # ─── Document Analysis ───────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def analyze_document(
        self,
        document_bytes: bytes,
        *,
        document_type: LegalDocumentType = LegalDocumentType.GENERAL,
        content_type: str = "application/pdf",
    ) -> dict[str, Any]:
        """Analyze a document using the Content Understanding service.

        Submits the document for analysis, polls until completion, and returns
        the extracted structured content.

        Args:
            document_bytes: Raw document bytes.
            document_type: Type of legal document (determines which analyzer is used).
            content_type: MIME type of the document.

        Returns:
            Extracted content including:
            - ``content``: Full extracted text.
            - ``pages``: Per-page content with text and layout information.
            - ``tables``: Extracted tables with cell data.
            - ``figures``: Extracted figures and diagrams.
            - ``fields``: Custom fields extracted by the analyzer.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.
            TimeoutError: If analysis does not complete within the timeout.
        """
        definition = _ANALYZER_DEFINITIONS[document_type]
        analyzer_id = definition["analyzerId"]

        logger.info(
            "analyze_document_start",
            analyzer_id=analyzer_id,
            doc_type=document_type.value,
            size_bytes=len(document_bytes),
            content_type=content_type,
        )

        # Step 1: Submit document for analysis
        operation_url = await self._submit_analysis(analyzer_id, document_bytes, content_type)

        # Step 2: Poll for results
        result = await self._poll_analysis_result(operation_url)

        logger.info(
            "analyze_document_complete",
            analyzer_id=analyzer_id,
            page_count=len(result.get("pages", [])),
            table_count=len(result.get("tables", [])),
            field_count=len(result.get("fields", {})),
        )

        return result

    async def _submit_analysis(
        self,
        analyzer_id: str,
        document_bytes: bytes,
        content_type: str,
    ) -> str:
        """Submit a document for analysis and return the operation-location URL.

        Args:
            analyzer_id: The analyzer to use.
            document_bytes: Raw document bytes.
            content_type: MIME type of the document.

        Returns:
            The operation-location URL to poll for results.
        """
        url = f"{self._endpoint}/contentunderstanding/analyzers/{analyzer_id}:analyze?api-version={_API_VERSION}"
        token = await self._credential.get_token("https://cognitiveservices.azure.com/.default")
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": content_type,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, content=document_bytes)
            response.raise_for_status()

        operation_location = response.headers.get("operation-location")
        if not operation_location:
            msg = "Content Understanding API did not return an operation-location header."
            raise RuntimeError(msg)

        logger.debug("analysis_submitted", operation_location=operation_location)
        return str(operation_location)

    async def _poll_analysis_result(self, operation_url: str) -> dict[str, Any]:
        """Poll an analysis operation until it completes or times out.

        Args:
            operation_url: The operation-location URL returned by the submit call.

        Returns:
            The final analysis result.

        Raises:
            TimeoutError: If the operation does not complete in time.
            RuntimeError: If the operation fails.
        """
        elapsed = 0.0

        async with httpx.AsyncClient(timeout=30.0) as client:
            while elapsed < _ANALYSIS_TIMEOUT_SECONDS:
                headers = await self._get_auth_headers()
                response = await client.get(operation_url, headers=headers)
                response.raise_for_status()

                body = response.json()
                status = body.get("status", "").lower()

                if status == "succeeded":
                    return self._normalize_result(body.get("result", body))

                if status in ("failed", "cancelled"):
                    error_detail = body.get("error", {}).get("message", "Unknown error")
                    msg = f"Content Understanding analysis {status}: {error_detail}"
                    raise RuntimeError(msg)

                # Still running — wait and retry
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                elapsed += _POLL_INTERVAL_SECONDS

        msg = f"Content Understanding analysis timed out after {_ANALYSIS_TIMEOUT_SECONDS}s"
        raise TimeoutError(msg)

    @staticmethod
    def _normalize_result(raw_result: dict[str, Any]) -> dict[str, Any]:
        """Normalize the raw API result into a consistent structure.

        Args:
            raw_result: The raw result object from the API response.

        Returns:
            Normalized result with consistent field names.
        """
        pages: list[dict[str, Any]] = []
        for page in raw_result.get("pages", []):
            pages.append(
                {
                    "page_number": page.get("pageNumber", 0),
                    "width": page.get("width", 0),
                    "height": page.get("height", 0),
                    "unit": page.get("unit", "pixel"),
                    "text": page.get("content", ""),
                    "lines": [
                        {
                            "content": line.get("content", ""),
                            "polygon": line.get("polygon", []),
                        }
                        for line in page.get("lines", [])
                    ],
                    "words": [
                        {
                            "content": word.get("content", ""),
                            "confidence": word.get("confidence", 0.0),
                        }
                        for word in page.get("words", [])
                    ],
                }
            )

        tables: list[dict[str, Any]] = []
        for table in raw_result.get("tables", []):
            tables.append(
                {
                    "row_count": table.get("rowCount", 0),
                    "column_count": table.get("columnCount", 0),
                    "cells": [
                        {
                            "row_index": cell.get("rowIndex", 0),
                            "column_index": cell.get("columnIndex", 0),
                            "content": cell.get("content", ""),
                            "kind": cell.get("kind", "content"),
                        }
                        for cell in table.get("cells", [])
                    ],
                }
            )

        figures: list[dict[str, Any]] = []
        for figure in raw_result.get("figures", []):
            figures.append(
                {
                    "caption": figure.get("caption", {}).get("content", ""),
                    "bounding_regions": figure.get("boundingRegions", []),
                }
            )

        return {
            "content": raw_result.get("content", ""),
            "pages": pages,
            "tables": tables,
            "figures": figures,
            "fields": raw_result.get("fields", {}),
            "page_count": len(pages),
        }
