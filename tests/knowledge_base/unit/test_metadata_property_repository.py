"""Unit tests for MetadataPropertyRepository."""

from __future__ import annotations

from typing import Any

import pytest

from by_qa.knowledge_base.repositories.metadata_property_repository import (
    MetadataPropertyRepository,
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
async def test_create_property_executes_insert():
    repo = MetadataPropertyRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 1,
                "property_name": "status",
                "value_type": "string",
                "description": "doc status",
                "ext_params": None,
            }
        ]
    )

    result = await repo.create(
        cursor,
        property_name="status",
        value_type="string",
        description="doc status",
        ext_params=None,
    )

    assert result is not None
    assert result["property_name"] == "status"
    sql = cursor.executed[0][0].lower()
    assert "insert into knowledge_metadata_property_def" in sql
    assert "returning" in sql


@pytest.mark.asyncio
async def test_get_by_name_filters_deleted():
    repo = MetadataPropertyRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 1,
                "property_name": "status",
                "value_type": "string",
                "description": None,
                "ext_params": None,
            }
        ]
    )

    result = await repo.get_by_name(cursor, "status")

    assert result is not None
    sql = cursor.executed[0][0].lower()
    assert "is_deleted = false" in sql


@pytest.mark.asyncio
async def test_soft_delete_sets_is_deleted():
    repo = MetadataPropertyRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 1}])

    await repo.soft_delete(cursor, property_name="status")

    sql = cursor.executed[0][0].lower()
    assert "is_deleted = true" in sql
    assert "property_name" in str(cursor.executed[0][1])


@pytest.mark.asyncio
async def test_list_all_returns_active_properties():
    repo = MetadataPropertyRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "kid": 1,
                    "property_name": "status",
                    "value_type": "string",
                    "description": None,
                    "ext_params": None,
                },
                {
                    "kid": 2,
                    "property_name": "tags",
                    "value_type": "stringList",
                    "description": None,
                    "ext_params": None,
                },
            ]
        ]
    )

    result = await repo.list_properties(cursor, property_names=None)

    assert len(result) == 2
    sql = cursor.executed[0][0].lower()
    assert "is_deleted = false" in sql


@pytest.mark.asyncio
async def test_list_filtered_by_names():
    repo = MetadataPropertyRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "kid": 1,
                    "property_name": "status",
                    "value_type": "string",
                    "description": None,
                    "ext_params": None,
                },
            ]
        ]
    )

    result = await repo.list_properties(cursor, property_names=["status"])

    assert len(result) == 1
    sql = cursor.executed[0][0].lower()
    assert "property_name" in sql
