"""Azure Blob Storage service client for raw document storage."""

from __future__ import annotations

import structlog
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from legalmind.config import get_async_azure_credential, get_settings

logger = structlog.get_logger(__name__)


class BlobStorageService:
    """Wrapper around Azure Blob Storage for legal document storage.

    Stores raw uploaded documents (PDF, DOCX, images) before processing.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._credential = get_async_azure_credential()
        self._account_url = settings.storage.account_url
        self._container_name = settings.storage.container

    def _get_client(self) -> BlobServiceClient:
        return BlobServiceClient(account_url=self._account_url, credential=self._credential)

    async def upload_document(
        self,
        blob_name: str,
        data: bytes,
        *,
        content_type: str = "application/pdf",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload a document to Blob Storage.

        Args:
            blob_name: Name/path for the blob (e.g., 'contracts/2024/doc.pdf').
            data: Raw file bytes.
            content_type: MIME type of the document.
            metadata: Optional metadata tags for the blob.

        Returns:
            The full URL of the uploaded blob.
        """
        logger.info("upload_document", blob_name=blob_name, size_bytes=len(data), content_type=content_type)

        async with self._get_client() as service_client:
            container_client = service_client.get_container_client(self._container_name)
            blob_client = container_client.get_blob_client(blob_name)

            await blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
                metadata=metadata,
            )

            return blob_client.url

    async def download_document(self, blob_name: str) -> bytes:
        """Download a document from Blob Storage.

        Args:
            blob_name: The blob name/path.

        Returns:
            The raw file bytes.
        """
        logger.info("download_document", blob_name=blob_name)

        async with self._get_client() as service_client:
            container_client = service_client.get_container_client(self._container_name)
            blob_client = container_client.get_blob_client(blob_name)

            stream = await blob_client.download_blob()
            return await stream.readall()

    async def list_documents(self, prefix: str | None = None) -> list[dict[str, str]]:
        """List documents in the storage container.

        Args:
            prefix: Optional path prefix to filter by (e.g., 'contracts/').

        Returns:
            List of blob metadata dicts with 'name', 'size', 'last_modified'.
        """
        async with self._get_client() as service_client:
            container_client = service_client.get_container_client(self._container_name)

            blobs: list[dict[str, str]] = []
            async for blob in container_client.list_blobs(name_starts_with=prefix):
                blobs.append(
                    {
                        "name": blob.name,
                        "size": str(blob.size),
                        "content_type": (blob.content_settings.content_type or "") if blob.content_settings else "",
                        "last_modified": blob.last_modified.isoformat() if blob.last_modified else "",
                    }
                )

            return blobs

    async def delete_document(self, blob_name: str) -> None:
        """Delete a document from Blob Storage.

        Args:
            blob_name: The blob name/path to delete.
        """
        logger.info("delete_document", blob_name=blob_name)

        async with self._get_client() as service_client:
            container_client = service_client.get_container_client(self._container_name)
            blob_client = container_client.get_blob_client(blob_name)
            await blob_client.delete_blob()

    async def get_blob_url(self, blob_name: str) -> str:
        """Get the URL for a blob.

        Args:
            blob_name: The blob name/path.

        Returns:
            The blob URL.
        """
        async with self._get_client() as service_client:
            container_client = service_client.get_container_client(self._container_name)
            blob_client = container_client.get_blob_client(blob_name)
            return blob_client.url
