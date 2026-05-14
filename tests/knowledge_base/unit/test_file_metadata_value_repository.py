"""Unit tests for FileMetadataValueRepository."""

from __future__ import annotations

from typing import Any

import pytest

from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
    FileMetadataValueRepository,
)


class FakeCursor:
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.executed: list[tuple[str, Any]] = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchone(self):
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    async def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []


@pytest.mark.asyncio
async def test_upsert_string_value():
    repo = FileMetadataValueRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 1}])

    await repo.upsert_value(
        cursor,
        fs_entry_id=10,
        knowledge_base_id=2,
        property_def_id=5,
        value_type="string",
        value="active",
    )

    sql = cursor.executed[0][0].lower()
    assert "knowledge_file_metadata_value" in sql
    assert "value_string" in sql


@pytest.mark.asyncio
async def test_upsert_string_list_value():
    repo = FileMetadataValueRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 1}])

    await repo.upsert_value(
        cursor,
        fs_entry_id=10,
        knowledge_base_id=2,
        property_def_id=6,
        value_type="stringList",
        value=["hr", "contract"],
    )

    sql = cursor.executed[0][0].lower()
    assert "value_string_list" in sql


@pytest.mark.asyncio
async def test_soft_delete_value():
    repo = FileMetadataValueRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 1}])

    await repo.soft_delete_value(cursor, fs_entry_id=10, property_def_id=5)

    sql = cursor.executed[0][0].lower()
    assert "is_deleted = true" in sql


@pytest.mark.asyncio
async def test_get_file_metadata_returns_all():
    repo = FileMetadataValueRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "property_name": "status",
                    "value_type": "string",
                    "value_string": "active",
                    "value_number": None,
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": None,
                },
                {
                    "property_name": "tags",
                    "value_type": "stringList",
                    "value_string": None,
                    "value_number": None,
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": ["hr", "contract"],
                },
            ]
        ]
    )

    result = await repo.get_file_metadata(cursor, fs_entry_id=10)

    assert len(result) == 2
    sql = cursor.executed[0][0].lower()
    assert "is_deleted = false" in sql
    assert "join" in sql


@pytest.mark.asyncio
async def test_list_used_properties():
    repo = FileMetadataValueRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "property_name": "status",
                    "value_type": "string",
                    "description": None,
                },
            ]
        ]
    )

    result = await repo.list_used_properties(cursor, knowledge_base_ids=[2, 3])

    assert len(result) == 1
    sql = cursor.executed[0][0].lower()
    assert "distinct" in sql or "group by" in sql
