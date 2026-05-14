"""Unit tests for MetadataPropertyService."""

from __future__ import annotations

from typing import Any

import pytest

from by_qa.knowledge_base.api.metadata_schemas import (
    CreateMetadataPropertyRequest,
    DeleteMetadataPropertyRequest,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


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
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self._cursor = cursor_obj or FakeCursor()

    def cursor(self):
        return self._cursor

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_create_property_success():
    cursor = FakeCursor(
        fetchone_results=[
            None,  # get_by_name returns None (not exists)
            {  # create returns the new row
                "kid": 1,
                "property_name": "status",
                "value_type": "string",
                "description": "doc status",
                "ext_params": None,
            },
        ]
    )
    conn = FakeConnection(cursor)

    from by_qa.knowledge_base.repositories.metadata_property_repository import (
        MetadataPropertyRepository,
    )
    from by_qa.knowledge_base.services.metadata_property_service import (
        MetadataPropertyService,
    )

    async def factory():
        return conn

    service = MetadataPropertyService(
        connection_factory=factory,
        metadata_property_repository=MetadataPropertyRepository(),
    )

    request = CreateMetadataPropertyRequest(
        property_name="status",
        value_type="string",
        description="doc status",
    )
    result = await service.create_property(request)

    assert result.property_name == "status"
    assert result.value_type == "string"
    assert conn.committed


@pytest.mark.asyncio
async def test_create_property_duplicate_raises():
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 1,
                "property_name": "status",
                "value_type": "string",
                "description": None,
                "ext_params": None,
            },
        ]
    )
    conn = FakeConnection(cursor)

    from by_qa.knowledge_base.repositories.metadata_property_repository import (
        MetadataPropertyRepository,
    )
    from by_qa.knowledge_base.services.metadata_property_service import (
        MetadataPropertyService,
    )

    async def factory():
        return conn

    service = MetadataPropertyService(
        connection_factory=factory,
        metadata_property_repository=MetadataPropertyRepository(),
    )

    request = CreateMetadataPropertyRequest(
        property_name="status",
        value_type="string",
    )

    with pytest.raises(KnowledgeBaseValidationError, match="already exists"):
        await service.create_property(request)

    assert conn.rolled_back


@pytest.mark.asyncio
async def test_delete_property_not_found_raises():
    cursor = FakeCursor(fetchone_results=[None])
    conn = FakeConnection(cursor)

    from by_qa.knowledge_base.repositories.metadata_property_repository import (
        MetadataPropertyRepository,
    )
    from by_qa.knowledge_base.services.metadata_property_service import (
        MetadataPropertyService,
    )

    async def factory():
        return conn

    service = MetadataPropertyService(
        connection_factory=factory,
        metadata_property_repository=MetadataPropertyRepository(),
    )

    request = DeleteMetadataPropertyRequest(property_name="nonexistent")

    with pytest.raises(KnowledgeBaseValidationError, match="not found"):
        await service.delete_property(request)


@pytest.mark.asyncio
async def test_create_property_system_field_conflict_raises():
    """Creating a property with a system field name should raise."""
    conn = FakeConnection(FakeCursor())

    from by_qa.knowledge_base.repositories.metadata_property_repository import (
        MetadataPropertyRepository,
    )
    from by_qa.knowledge_base.services.metadata_property_service import (
        MetadataPropertyService,
    )

    async def factory():
        return conn

    service = MetadataPropertyService(
        connection_factory=factory,
        metadata_property_repository=MetadataPropertyRepository(),
    )

    request = CreateMetadataPropertyRequest(
        property_name="filePath",
        value_type="string",
    )

    with pytest.raises(
        KnowledgeBaseValidationError, match="conflicts with system field"
    ):
        await service.create_property(request)


@pytest.mark.asyncio
async def test_delete_property_system_property_raises():
    """Deleting a system property (is_system=True) should raise."""
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 1,
                "property_name": "fileName",
                "value_type": "string",
                "description": None,
                "ext_params": None,
                "is_system": True,
            },
        ]
    )
    conn = FakeConnection(cursor)

    from by_qa.knowledge_base.repositories.metadata_property_repository import (
        MetadataPropertyRepository,
    )
    from by_qa.knowledge_base.services.metadata_property_service import (
        MetadataPropertyService,
    )

    async def factory():
        return conn

    service = MetadataPropertyService(
        connection_factory=factory,
        metadata_property_repository=MetadataPropertyRepository(),
    )

    request = DeleteMetadataPropertyRequest(property_name="fileName")

    with pytest.raises(
        KnowledgeBaseValidationError, match="cannot delete system metadata"
    ):
        await service.delete_property(request)
