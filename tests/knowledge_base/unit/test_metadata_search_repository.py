"""Unit tests for MetadataSearchRepository."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from by_qa.knowledge_base.repositories.metadata_search_repository import (
    MetadataSearchRepository,
)


class FakeCursor:
    def __init__(self, fetchall_results=None):
        self.executed: list[tuple[str, Any]] = []
        self._fetchall_results = list(fetchall_results or [])

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []


@pytest.mark.asyncio
async def test_search_without_where():
    repo = MetadataSearchRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {"kid": 10, "kb_code": "2", "full_path": "docs/test.md"},
            ]
        ]
    )

    results = await repo.search_files(
        cursor,
        kb_ids=[2],
        where_sql="",
        where_params={},
        limit=20,
    )

    assert len(results) == 1
    sql = cursor.executed[0][0].lower()
    assert "knowledge_fs_entry" in sql
    assert "ltrim(fe.virtual_path, '/') as full_path" in sql
    assert "limit" in sql


@pytest.mark.asyncio
async def test_search_with_where_clause():
    repo = MetadataSearchRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {"kid": 10, "kb_code": "2", "full_path": "docs/test.md"},
            ]
        ]
    )

    results = await repo.search_files(
        cursor,
        kb_ids=[2],
        where_sql="EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv WHERE mv.fs_entry_id = fe.kid AND mv.property_def_id = %(dsl_p1)s AND mv.is_deleted = false AND mv.value_string = %(dsl_p2)s)",
        where_params={"dsl_p1": 1, "dsl_p2": "active"},
        limit=20,
    )

    assert len(results) == 1
    sql = cursor.executed[0][0].lower()
    assert "dsl_p1" in sql or "dsl_p1" in str(cursor.executed[0][1])


@pytest.mark.asyncio
async def test_search_with_like_escape_clause_keeps_sql_intact():
    repo = MetadataSearchRepository()
    cursor = FakeCursor(fetchall_results=[[]])

    await repo.search_files(
        cursor,
        kb_ids=[2],
        where_sql="(fe.virtual_path LIKE %(dsl_p1)s ESCAPE '!')",
        where_params={"dsl_p1": "/docs/!_100!%"},
        limit=20,
    )

    sql, params = cursor.executed[0]
    assert "escape '!'" in sql.lower()
    assert params["dsl_p1"] == "/docs/!_100!%"


@pytest.mark.asyncio
async def test_search_returns_metadata_fields():
    repo = MetadataSearchRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [{"kid": 10, "kb_code": "2", "full_path": "docs/test.md"}],
            [
                {
                    "fs_entry_id": 10,
                    "property_name": "status",
                    "value_type": "string",
                    "value_string": "active",
                    "value_number": None,
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": None,
                },
            ],
        ]
    )

    results = await repo.search_files(
        cursor,
        kb_ids=[2],
        where_sql="",
        where_params={},
        limit=20,
    )

    assert len(results) == 1


@pytest.mark.asyncio
async def test_backfill_metadata():
    repo = MetadataSearchRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "fs_entry_id": 10,
                    "property_name": "status",
                    "value_type": "string",
                    "value_string": "active",
                    "value_number": None,
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": None,
                },
            ]
        ]
    )

    result = await repo.backfill_metadata(
        cursor,
        fs_entry_ids=[10],
        property_names=["status"],
    )

    assert 10 in result
    assert "status" in result[10]
    sql = cursor.executed[0][0].lower()
    assert "knowledge_file_metadata_value" in sql


@pytest.mark.asyncio
async def test_backfill_metadata_converts_decimal_numbers_to_json_safe_primitives():
    repo = MetadataSearchRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "fs_entry_id": 10,
                    "property_name": "budget",
                    "value_type": "number",
                    "value_string": None,
                    "value_number": Decimal("12.5"),
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": None,
                },
                {
                    "fs_entry_id": 10,
                    "property_name": "count",
                    "value_type": "number",
                    "value_string": None,
                    "value_number": Decimal("7"),
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": None,
                },
            ]
        ]
    )

    result = await repo.backfill_metadata(
        cursor,
        fs_entry_ids=[10],
        property_names=["budget", "count"],
    )

    assert result[10]["budget"]["value"] == 12.5
    assert isinstance(result[10]["budget"]["value"], float)
    assert result[10]["count"]["value"] == 7
    assert isinstance(result[10]["count"]["value"], int)


@pytest.mark.asyncio
async def test_backfill_metadata_preserves_other_supported_json_safe_types():
    repo = MetadataSearchRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "fs_entry_id": 10,
                    "property_name": "status",
                    "value_type": "string",
                    "value_string": "active",
                    "value_number": None,
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": None,
                },
                {
                    "fs_entry_id": 10,
                    "property_name": "enabled",
                    "value_type": "boolean",
                    "value_string": None,
                    "value_number": None,
                    "value_boolean": True,
                    "value_datetime": None,
                    "value_string_list": None,
                },
                {
                    "fs_entry_id": 10,
                    "property_name": "effectiveAt",
                    "value_type": "datetime",
                    "value_string": None,
                    "value_number": None,
                    "value_boolean": None,
                    "value_datetime": FakeDateTime("2026-05-19T10:00:00+00:00"),
                    "value_string_list": None,
                },
                {
                    "fs_entry_id": 10,
                    "property_name": "tags",
                    "value_type": "stringList",
                    "value_string": None,
                    "value_number": None,
                    "value_boolean": None,
                    "value_datetime": None,
                    "value_string_list": ["hr", "policy"],
                },
            ]
        ]
    )

    result = await repo.backfill_metadata(
        cursor,
        fs_entry_ids=[10],
        property_names=["status", "enabled", "effectiveAt", "tags"],
    )

    assert result[10]["status"]["value"] == "active"
    assert result[10]["enabled"]["value"] is True
    assert result[10]["effectiveAt"]["value"] == "2026-05-19T10:00:00+00:00"
    assert result[10]["tags"]["value"] == ["hr", "policy"]


class FakeDateTime:
    def __init__(self, value: str):
        self.value = value

    def isoformat(self) -> str:
        return self.value
