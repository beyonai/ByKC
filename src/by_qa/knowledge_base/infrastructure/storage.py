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
]
