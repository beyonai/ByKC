"""Unit tests for transactional file metadata updates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from by_qa.knowledge_base.api.metadata_schemas import UpdateFileMetadataRequest
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.file_metadata_update_service import (
    FileMetadataUpdateService,
)


class FakeConnection:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return object()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        self.closed = True


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor: Any, kb_code: str):
        return {"kid": 2, "kb_code": kb_code}


class FakeFsEntryRepository:
    def __init__(self):
        self.paths: list[str] = []

    async def get_file_by_path_for_update(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ):
        self.paths.append(full_path)
        return {"kid": 10, "knowledge_base_id": knowledge_base_id}


class FakeMetadataRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.deleted: list[str] = []
        self.upserts: list[dict[str, Any]] = []

    async def get_file_metadata(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_names: list[str] | None = None,
    ):  # pylint: disable=unused-argument
        return [row for row in self.rows if row["property_name"] in property_names]

    async def soft_delete_value(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_name: str,
        value_type: str | None = None,
    ):  # pylint: disable=unused-argument
        self.deleted.append(property_name)

    async def upsert_value(self, cursor: Any, **kwargs):
        self.upserts.append(kwargs)


def _service(connection: FakeConnection, metadata_repository: FakeMetadataRepository):
    async def connection_factory():
        return connection

    return FileMetadataUpdateService(
        connection_factory=connection_factory,
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=FakeFsEntryRepository(),
        file_metadata_value_repository=metadata_repository,
    )


@pytest.mark.asyncio
async def test_update_metadata_applies_batch_and_soft_deletes_changed_type():
    connection = FakeConnection()
    repository = FakeMetadataRepository(
        [
            {
                "property_name": "status",
                "value_type": "number",
                "value_string_list": None,
            },
            {
                "property_name": "tags",
                "value_type": "stringList",
                "value_string_list": '["existing", "contract"]',
            },
        ]
    )
    service = _service(connection, repository)

    await service.update_metadata(
        UpdateFileMetadataRequest.model_validate(
            {
                "knCode": "2",
                "filePath": "/docs/a.md",
                "operationList": [
                    {
                        "propertyName": "status",
                        "operation": "set",
                        "valueType": "string",
                        "value": "active",
                    },
                    {
                        "propertyName": "tags",
                        "operation": "append",
                        "value": ["contract", "renewal"],
                    },
                    {"propertyName": "owner", "operation": "unset"},
                    {
                        "propertyName": "publishedAt",
                        "operation": "set",
                        "valueType": "datetime",
                        "value": "2026-08-10T12:30:00Z",
                    },
                ],
            }
        )
    )

    assert connection.committed is True
    assert connection.rolled_back is False
    assert connection.closed is True
    assert repository.deleted == ["status", "owner"]
    assert repository.upserts[0]["value"] == "active"
    assert repository.upserts[1]["value"] == ["existing", "contract", "renewal"]
    assert repository.upserts[2]["value"] == datetime.fromisoformat(
        "2026-08-10T12:30:00+00:00"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "value", "expected"),
    [
        ("remove", ["missing", "a"], ["b"]),
        ("clear", None, []),
    ],
)
async def test_update_metadata_modifies_existing_string_list(
    operation: str, value: list[str] | None, expected: list[str]
):
    connection = FakeConnection()
    repository = FakeMetadataRepository(
        [
            {
                "property_name": "tags",
                "value_type": "stringList",
                "value_string_list": ["a", "b"],
            }
        ]
    )
    body: dict[str, Any] = {
        "propertyName": "tags",
        "operation": operation,
    }
    if value is not None:
        body["value"] = value

    await _service(connection, repository).update_metadata(
        UpdateFileMetadataRequest.model_validate(
            {
                "knCode": "2",
                "filePath": "/a.md",
                "operationList": [body],
            }
        )
    )

    assert repository.upserts[0]["value"] == expected


@pytest.mark.asyncio
async def test_update_metadata_rolls_back_when_operation_is_not_applicable():
    connection = FakeConnection()
    repository = FakeMetadataRepository()

    with pytest.raises(
        KnowledgeBaseValidationError,
        match="operation append requires an existing stringList value: tags",
    ):
        await _service(connection, repository).update_metadata(
            UpdateFileMetadataRequest.model_validate(
                {
                    "knCode": "2",
                    "filePath": "/a.md",
                    "operationList": [
                        {
                            "propertyName": "tags",
                            "operation": "append",
                            "value": ["new"],
                        }
                    ],
                }
            )
        )

    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True


@pytest.mark.asyncio
async def test_update_metadata_rejects_read_only_system_field():
    connection = FakeConnection()

    with pytest.raises(
        KnowledgeBaseValidationError,
        match="metadata field is read-only: fileName",
    ):
        await _service(connection, FakeMetadataRepository()).update_metadata(
            UpdateFileMetadataRequest.model_validate(
                {
                    "knCode": "2",
                    "filePath": "/a.md",
                    "operationList": [
                        {
                            "propertyName": "fileName",
                            "operation": "set",
                            "valueType": "string",
                            "value": "b.md",
                        }
                    ],
                }
            )
        )

    assert connection.rolled_back is True
