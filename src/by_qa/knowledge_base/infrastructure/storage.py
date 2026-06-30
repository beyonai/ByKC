"""Standard storage protocol layer for the knowledge module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StorageLocation:
    namespace: str
    key: str


@dataclass(frozen=True)
class StoredObject:
    location: StorageLocation
    size: int | None = None
    checksum: str | None = None
    content_type: str | None = None


class StorageError(Exception):
    retryable: bool = False


class StorageConfigurationError(StorageError):
    pass


class StorageAuthenticationError(StorageError):
    pass


class StorageNotFoundError(StorageError):
    pass


class StorageConflictError(StorageError):
    pass


class StorageOperationError(StorageError):
    pass


@runtime_checkable
class KnowledgeStorageProvider(Protocol):
    provider_name: str
    storage_path_bound_to_logical_path: bool

    async def ensure_ready(self) -> None: ...

    def build_original_location(
        self,
        *,
        kb_code: str,
        knowledge_base_id: int,
        fs_entry_id: int,
        file_path: str,
        mime_type: str,
    ) -> StorageLocation: ...

    def build_markdown_location(
        self,
        *,
        kb_code: str,
        knowledge_base_id: int,
        fs_entry_id: int,
        file_path: str,
    ) -> StorageLocation: ...

    async def write(
        self,
        location: StorageLocation,
        content: bytes,
        *,
        content_type: str,
    ) -> StoredObject: ...

    async def read(self, location: StorageLocation) -> bytes: ...

    async def delete(self, location: StorageLocation) -> None: ...

    async def delete_quietly(self, location: StorageLocation) -> None: ...

    async def move(
        self,
        source: StorageLocation,
        target: StorageLocation,
        *,
        overwrite: bool = False,
    ) -> None: ...


def load_storage_provider() -> "KnowledgeStorageProvider":
    """Load the configured storage provider, falling back to default S3 implementation."""
    from importlib import import_module
    from os import getenv

    provider_path = getenv("BY_QA_STORAGE_PROVIDER", "").strip()
    if not provider_path:
        from by_qa.knowledge_base.infrastructure.storage_s3 import (
            build_s3_storage_provider,
        )

        return build_s3_storage_provider()

    module_name, separator, attribute_name = provider_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(
            "BY_QA_STORAGE_PROVIDER must use the 'module:attribute' format."
        )

    module = import_module(module_name)
    provider_factory = getattr(module, attribute_name)
    provider = provider_factory() if callable(provider_factory) else provider_factory
    if not isinstance(provider, KnowledgeStorageProvider):
        raise TypeError(
            "BY_QA_STORAGE_PROVIDER must resolve to a KnowledgeStorageProvider."
        )
    return provider


__all__ = [
    "KnowledgeStorageProvider",
    "StorageAuthenticationError",
    "StorageConfigurationError",
    "StorageConflictError",
    "StorageError",
    "StorageLocation",
    "StorageNotFoundError",
    "StorageOperationError",
    "StoredObject",
    "load_storage_provider",
]
