"""Transactional batch updates for one file's custom metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from by_qa.knowledge_base.api.metadata_schemas import (
    MetadataOperation,
    UpdateFileMetadataRequest,
)
from by_qa.knowledge_base.metadata_types import SYSTEM_FIELD_VALUE_TYPES
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class FileMetadataUpdateService:
    """Apply a batch of metadata operations atomically to one live file."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    file_metadata_value_repository: Any

    async def update_metadata(self, request: UpdateFileMetadataRequest) -> None:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if knowledge_base is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )

            knowledge_base_id = knowledge_base["kid"]
            file_entry = (
                await self.knowledge_fs_entry_repository.get_file_by_path_for_update(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=request.file_path.strip("/"),
                )
            )
            if file_entry is None:
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )

            for operation in request.operation_list:
                if operation.property_name in SYSTEM_FIELD_VALUE_TYPES:
                    raise KnowledgeBaseValidationError(
                        f"metadata field is read-only: {operation.property_name}"
                    )

            fs_entry_id = file_entry["kid"]
            property_names = [item.property_name for item in request.operation_list]
            rows = await self.file_metadata_value_repository.get_file_metadata(
                cursor,
                fs_entry_id=fs_entry_id,
                property_names=property_names,
            )
            current: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                current.setdefault(row["property_name"], []).append(row)

            for operation in request.operation_list:
                await self._apply_operation(
                    cursor,
                    operation=operation,
                    current_rows=current.get(operation.property_name, []),
                    fs_entry_id=fs_entry_id,
                    knowledge_base_id=knowledge_base_id,
                )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _apply_operation(
        self,
        cursor: Any,
        *,
        operation: MetadataOperation,
        current_rows: list[dict[str, Any]],
        fs_entry_id: int,
        knowledge_base_id: int,
    ) -> None:
        property_name = operation.property_name
        if operation.operation == "unset":
            await self.file_metadata_value_repository.soft_delete_value(
                cursor,
                fs_entry_id=fs_entry_id,
                property_name=property_name,
            )
            return

        if operation.operation == "set":
            assert operation.value_type is not None
            if len(current_rows) > 1 or (
                current_rows and current_rows[0]["value_type"] != operation.value_type
            ):
                await self.file_metadata_value_repository.soft_delete_value(
                    cursor,
                    fs_entry_id=fs_entry_id,
                    property_name=property_name,
                )
            await self.file_metadata_value_repository.upsert_value(
                cursor,
                fs_entry_id=fs_entry_id,
                knowledge_base_id=knowledge_base_id,
                property_name=property_name,
                value_type=operation.value_type,
                value=_normalize_value(operation.value, operation.value_type),
            )
            return

        if len(current_rows) != 1 or current_rows[0]["value_type"] != "stringList":
            raise KnowledgeBaseValidationError(
                f"operation {operation.operation} requires an existing "
                f"stringList value: {property_name}"
            )

        current_value = _string_list_value(current_rows[0])
        if operation.operation == "append":
            updated_value = list(current_value)
            for item in operation.value:
                if item not in updated_value:
                    updated_value.append(item)
        elif operation.operation == "remove":
            removed = set(operation.value)
            updated_value = [item for item in current_value if item not in removed]
        else:
            updated_value = []

        await self.file_metadata_value_repository.upsert_value(
            cursor,
            fs_entry_id=fs_entry_id,
            knowledge_base_id=knowledge_base_id,
            property_name=property_name,
            value_type="stringList",
            value=updated_value,
        )


def _normalize_value(value: Any, value_type: str) -> Any:
    if value_type == "datetime":
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _string_list_value(row: dict[str, Any]) -> list[str]:
    value = row["value_string_list"]
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise KnowledgeBaseValidationError(
            f"invalid stored stringList value: {row['property_name']}"
        )
    return value
