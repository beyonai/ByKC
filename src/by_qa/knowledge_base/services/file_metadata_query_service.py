"""Read-only service for file metadata values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from by_qa.knowledge_base.api.metadata_schemas import GetFileMetadataRequest
from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
    FileMetadataValueRepository,
)
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class FileMetadataQueryService:
    """Query file metadata without owning metadata definitions or updates."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: KnowledgeBaseRepository
    knowledge_fs_entry_repository: KnowledgeFsEntryRepository
    file_metadata_value_repository: FileMetadataValueRepository

    async def get_metadata(self, request: GetFileMetadataRequest) -> dict[str, Any]:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if kb is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )

            file_entry = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=kb["kid"],
                full_path=request.file_path,
            )
            if file_entry is None:
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )

            rows = await self.file_metadata_value_repository.get_file_metadata(
                cursor,
                fs_entry_id=file_entry["kid"],
                property_names=request.metadata_field_list,
            )
            return _format_metadata(rows)
        finally:
            await connection.close()


def _format_metadata(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for row in rows:
        metadata[row["property_name"]] = {
            "valueType": row["value_type"],
            "value": _extract_value(row),
        }
    return metadata


def _extract_value(row: dict[str, Any]) -> Any:
    value_type = row["value_type"]
    if value_type == "string":
        return row["value_string"]
    if value_type == "number":
        value = row["value_number"]
        return float(value) if hasattr(value, "as_tuple") else value
    if value_type == "boolean":
        return row["value_boolean"]
    if value_type == "datetime":
        value = row["value_datetime"]
        return value.isoformat() if hasattr(value, "isoformat") else value
    if value_type == "stringList":
        return row["value_string_list"]
    return None
