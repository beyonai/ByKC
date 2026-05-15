"""Service for file-level metadata CRUD operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.metadata_schemas import (
    GetFileMetadataRequest,
    ListMetadataFieldsRequest,
    MetadataPropertyResponse,
    UpdateFileMetadataRequest,
)
from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
    FileMetadataValueRepository,
)
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.repositories.metadata_property_repository import (
    MetadataPropertyRepository,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError

SCALAR_OPERATIONS = {"set", "unset"}
LIST_OPERATIONS = {"set", "unset", "append", "remove", "clear"}


def _extract_value(row: dict[str, Any]) -> Any:
    vt = row["value_type"]
    if vt == "string":
        return row["value_string"]
    elif vt == "number":
        return row["value_number"]
    elif vt == "boolean":
        return row["value_boolean"]
    elif vt == "datetime":
        dt = row["value_datetime"]
        return dt.isoformat() if dt else None
    elif vt == "stringList":
        return row["value_string_list"] or []
    return None


@dataclass
class FileMetadataService:
    """Manages file-level metadata values."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: KnowledgeBaseRepository
    knowledge_fs_entry_repository: KnowledgeFsEntryRepository
    metadata_property_repository: MetadataPropertyRepository
    file_metadata_value_repository: FileMetadataValueRepository

    async def _resolve_file(
        self, cursor: Any, kb_code: str, file_path: str
    ) -> tuple[int, int]:
        kb = await self.knowledge_base_repository.get_by_code(cursor, kb_code)
        if kb is None:
            raise KnowledgeBaseValidationError(f"knowledge base not found: {kb_code}")
        kb_id = kb["kid"]
        entry = await self.knowledge_fs_entry_repository.get_file_by_path(
            cursor, knowledge_base_id=kb_id, full_path=file_path
        )
        if entry is None:
            raise KnowledgeBaseValidationError(f"file not found: {file_path}")
        return kb_id, entry["kid"]

    async def update_metadata(
        self, request: UpdateFileMetadataRequest
    ) -> dict[str, Any]:
        logger.info(
            "file_metadata_service.update started: kb_code=%s, file_path=%s, ops=%s",
            request.kb_code,
            request.file_path,
            len(request.operation_list),
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_id, fs_entry_id = await self._resolve_file(
                cursor, request.kb_code, request.file_path
            )

            modified_metadata: dict[str, Any] = {}

            for op in request.operation_list:
                prop = await self.metadata_property_repository.get_by_name(
                    cursor, op.property_name
                )
                if prop is None:
                    raise KnowledgeBaseValidationError(
                        f"metadata property not defined: {op.property_name}"
                    )
                value_type = prop["value_type"]
                prop_def_id = prop["kid"]

                allowed_ops = (
                    LIST_OPERATIONS if value_type == "stringList" else SCALAR_OPERATIONS
                )
                if op.operation not in allowed_ops:
                    raise KnowledgeBaseValidationError(
                        f"operation {op.operation} is not allowed for "
                        f"property type: {value_type}"
                    )

                if op.operation == "unset":
                    await self.file_metadata_value_repository.soft_delete_value(
                        cursor,
                        fs_entry_id=fs_entry_id,
                        property_def_id=prop_def_id,
                    )
                elif op.operation == "set":
                    await self.file_metadata_value_repository.upsert_value(
                        cursor,
                        fs_entry_id=fs_entry_id,
                        knowledge_base_id=kb_id,
                        property_def_id=prop_def_id,
                        value_type=value_type,
                        value=op.value,
                    )
                elif op.operation == "clear":
                    await self.file_metadata_value_repository.upsert_value(
                        cursor,
                        fs_entry_id=fs_entry_id,
                        knowledge_base_id=kb_id,
                        property_def_id=prop_def_id,
                        value_type=value_type,
                        value=[],
                    )
                elif op.operation in ("append", "remove"):
                    existing = (
                        await self.file_metadata_value_repository.get_active_value(
                            cursor,
                            fs_entry_id=fs_entry_id,
                            property_def_id=prop_def_id,
                        )
                    )
                    current_list = (
                        existing["value_string_list"] if existing else []
                    ) or []
                    if op.operation == "append":
                        for item in op.value:
                            if item not in current_list:
                                current_list.append(item)
                    else:  # remove
                        current_list = [x for x in current_list if x not in op.value]
                    await self.file_metadata_value_repository.upsert_value(
                        cursor,
                        fs_entry_id=fs_entry_id,
                        knowledge_base_id=kb_id,
                        property_def_id=prop_def_id,
                        value_type=value_type,
                        value=current_list,
                    )

                if op.operation != "unset":
                    row = await self.file_metadata_value_repository.get_active_value(
                        cursor,
                        fs_entry_id=fs_entry_id,
                        property_def_id=prop_def_id,
                    )
                    if row:
                        modified_metadata[op.property_name] = {
                            "valueType": value_type,
                            "value": _extract_value(row),
                        }

            await connection.commit()
            logger.info(
                "file_metadata_service.update committed: fs_entry_id=%s",
                fs_entry_id,
            )
            return modified_metadata
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_metadata(self, request: GetFileMetadataRequest) -> dict[str, Any]:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            _, fs_entry_id = await self._resolve_file(
                cursor, request.kb_code, request.file_path
            )
            rows = await self.file_metadata_value_repository.get_file_metadata(
                cursor,
                fs_entry_id=fs_entry_id,
                property_names=request.metadata_field_list,
            )
            metadata: dict[str, Any] = {}
            for row in rows:
                metadata[row["property_name"]] = {
                    "valueType": row["value_type"],
                    "value": _extract_value(row),
                }
            return metadata
        finally:
            await connection.close()

    async def list_metadata_fields(
        self, request: ListMetadataFieldsRequest
    ) -> list[MetadataPropertyResponse]:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_ids: list[int] = []
            for code in request.kb_code_list:
                kb = await self.knowledge_base_repository.get_by_code(cursor, code)
                if kb is None:
                    raise KnowledgeBaseValidationError(
                        f"knowledge base not found: {code}"
                    )
                kb_ids.append(kb["kid"])

            rows = await self.file_metadata_value_repository.list_used_properties(
                cursor, knowledge_base_ids=kb_ids
            )
            return [
                MetadataPropertyResponse(
                    property_name=row["property_name"],
                    value_type=row["value_type"],
                    description=row["description"],
                )
                for row in rows
            ]
        finally:
            await connection.close()
