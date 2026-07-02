"""Unit tests for read-only file metadata query service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from by_qa.knowledge_base.api.metadata_schemas import GetFileMetadataRequest
from by_qa.knowledge_base.services.file_metadata_query_service import (
    FileMetadataQueryService,
)


class FakeConnection:
    def cursor(self):
        return object()

    async def close(self):
        pass


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor: Any, kb_code: str):
        return {"kid": 2, "kb_code": kb_code}


class FakeFsEntryRepository:
    async def get_file_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ):
        return {
            "kid": 10,
            "knowledge_base_id": knowledge_base_id,
            "virtual_path": full_path,
        }


class FakeFileMetadataValueRepository:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def get_file_metadata(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_names: list[str] | None = None,
    ):
        self.calls.append(
            {"fs_entry_id": fs_entry_id, "property_names": property_names}
        )
        return [
            {
                "property_name": "会议主题",
                "value_type": "string",
                "value_string": "DataCloud平台需求确认会",
                "value_number": None,
                "value_boolean": None,
                "value_datetime": None,
                "value_string_list": None,
            },
            {
                "property_name": "会议日期",
                "value_type": "datetime",
                "value_string": None,
                "value_number": None,
                "value_boolean": None,
                "value_datetime": datetime(2026, 5, 25),
                "value_string_list": None,
            },
        ]


@pytest.mark.asyncio
async def test_get_metadata_returns_formatted_file_metadata():
    metadata_repo = FakeFileMetadataValueRepository()

    async def connection_factory():
        return FakeConnection()

    service = FileMetadataQueryService(
        connection_factory=connection_factory,
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=FakeFsEntryRepository(),
        file_metadata_value_repository=metadata_repo,
    )

    result = await service.get_metadata(
        GetFileMetadataRequest(
            kb_code="1",
            file_path="/1.md",
            metadata_field_list=["会议主题", "会议日期"],
        )
    )

    assert result == {
        "会议主题": {
            "valueType": "string",
            "value": "DataCloud平台需求确认会",
        },
        "会议日期": {
            "valueType": "datetime",
            "value": "2026-05-25T00:00:00",
        },
    }
    assert metadata_repo.calls == [
        {
            "fs_entry_id": 10,
            "property_names": ["会议主题", "会议日期"],
        }
    ]
