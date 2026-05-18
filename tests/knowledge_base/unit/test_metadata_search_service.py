"""Unit tests for MetadataSearchService."""

from __future__ import annotations

from typing import Any

import pytest

from by_qa.knowledge_base.api.metadata_schemas import MetadataSearchRequest
from by_qa.knowledge_base.services.metadata_search_service import MetadataSearchService


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


class FakeConnection:
    def __init__(self, cursor_obj=None):
        self._cursor = cursor_obj or FakeCursor()

    def cursor(self):
        return self._cursor

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_metadata_search_no_where():
    cursor = FakeCursor(
        fetchone_results=[{"kid": 2}],  # kb lookup
        fetchall_results=[
            # search_files result
            [{"kid": 10, "kb_code": "2", "full_path": "docs/test.md"}],
            # backfill metadata
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
                }
            ],
        ],
    )
    conn = FakeConnection(cursor)

    from by_qa.knowledge_base.repositories.knowledge_base_repository import (
        KnowledgeBaseRepository,
    )
    from by_qa.knowledge_base.repositories.metadata_property_repository import (
        MetadataPropertyRepository,
    )
    from by_qa.knowledge_base.repositories.metadata_search_repository import (
        MetadataSearchRepository,
    )

    async def _get_conn():
        return conn

    service = MetadataSearchService(
        connection_factory=_get_conn,
        knowledge_base_repository=KnowledgeBaseRepository(),
        metadata_property_repository=MetadataPropertyRepository(),
        metadata_search_repository=MetadataSearchRepository(),
    )

    request = MetadataSearchRequest(
        kb_code_list=["2"],
        top_k=20,
        metadata_field_list=["status"],
        where={"exists": {"fieldName": "fileName"}},
    )
    results = await service.search(request)

    assert len(results) == 1
    assert results[0].kb_code == "2"


def test_metadata_search_request_requires_where():
    """`where` is mandatory so callers cannot accidentally request a full scan."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MetadataSearchRequest(kb_code_list=["1"], top_k=10)


def test_metadata_search_request_requires_kb_code_list():
    """`knCodeList` is mandatory and must be non-empty."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MetadataSearchRequest(where={"exists": {"fieldName": "fileName"}}, top_k=10)
    with pytest.raises(ValidationError):
        MetadataSearchRequest(
            kb_code_list=[],
            where={"exists": {"fieldName": "fileName"}},
            top_k=10,
        )


def test_metadata_search_request_top_k_defaults_to_500():
    request = MetadataSearchRequest(
        kb_code_list=["1"],
        where={"exists": {"fieldName": "fileName"}},
    )
    assert request.top_k == 500


def test_metadata_search_request_rejects_top_k_above_cap():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MetadataSearchRequest(
            kb_code_list=["1"],
            where={"exists": {"fieldName": "fileName"}},
            top_k=10001,
        )


def test_metadata_search_request_rejects_top_k_zero():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MetadataSearchRequest(
            kb_code_list=["1"],
            where={"exists": {"fieldName": "fileName"}},
            top_k=0,
        )
