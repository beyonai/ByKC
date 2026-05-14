"""Unit tests for FileMetadataService."""

from __future__ import annotations

from typing import Any

import pytest

from by_qa.knowledge_base.api.metadata_schemas import (
    MetadataOperation,
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
from by_qa.knowledge_base.services.file_metadata_service import FileMetadataService


class FakeCursor:
    """Cursor returned from the fake connection for repository methods that
    call cursor methods directly (e.g., execute/fetchone)."""

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


# ---------------------------------------------------------------------------
# Helper: builds a minimal FileMetadataService with overridden repos
# ---------------------------------------------------------------------------


def _make_service(
    *,
    cursor: FakeCursor | None = None,
    kb_repo: KnowledgeBaseRepository | None = None,
    entry_repo: KnowledgeFsEntryRepository | None = None,
    prop_repo: MetadataPropertyRepository | None = None,
    value_repo: FileMetadataValueRepository | None = None,
) -> tuple[FileMetadataService, FakeConnection]:
    conn = FakeConnection(cursor)

    async def _factory():
        return conn

    service = FileMetadataService(
        connection_factory=_factory,
        knowledge_base_repository=kb_repo or KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=entry_repo or KnowledgeFsEntryRepository(),
        metadata_property_repository=prop_repo or MetadataPropertyRepository(),
        file_metadata_value_repository=value_repo or FileMetadataValueRepository(),
    )
    return service, conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_metadata_set_string():
    """set operation on a string property should upsert the value."""

    class MockKBRepo(KnowledgeBaseRepository):
        async def get_by_code(self, cursor, kb_code):
            return {"kid": 2}

    class MockEntryRepo(KnowledgeFsEntryRepository):
        async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
            return {"kid": 10, "knowledge_base_id": 2}

    class MockPropRepo(MetadataPropertyRepository):
        async def get_by_name(self, cursor, property_name):
            return {
                "kid": 5,
                "property_name": "status",
                "value_type": "string",
                "description": None,
                "ext_params": None,
            }

    class MockValueRepo(FileMetadataValueRepository):
        def __init__(self):
            self.upsert_calls: list[dict[str, Any]] = []
            self.get_active_calls: list[dict[str, Any]] = []

        async def upsert_value(
            self,
            cursor,
            *,
            fs_entry_id,
            knowledge_base_id,
            property_def_id,
            value_type,
            value,
        ):
            self.upsert_calls.append(locals())
            return {"kid": 100}

        async def get_active_value(self, cursor, *, fs_entry_id, property_def_id):
            self.get_active_calls.append(locals())
            return {
                "kid": 100,
                "value_string": "active",
                "value_number": None,
                "value_boolean": None,
                "value_datetime": None,
                "value_string_list": None,
                "property_name": "status",
                "value_type": "string",
            }

        async def soft_delete_value(self, cursor, *, fs_entry_id, property_def_id):
            pass

    service, conn = _make_service(
        kb_repo=MockKBRepo(),
        entry_repo=MockEntryRepo(),
        prop_repo=MockPropRepo(),
        value_repo=MockValueRepo(),
    )

    request = UpdateFileMetadataRequest(
        kb_code="2",
        file_path="/docs/test.md",
        operation_list=[
            MetadataOperation(property_name="status", operation="set", value="active"),
        ],
    )
    result = await service.update_metadata(request)

    assert conn.committed
    assert "status" in result
    assert result["status"]["value"] == "active"
    assert result["status"]["valueType"] == "string"


@pytest.mark.asyncio
async def test_update_metadata_invalid_operation_for_type():
    """append on a string property should raise validation error."""

    class MockKBRepo(KnowledgeBaseRepository):
        async def get_by_code(self, cursor, kb_code):
            return {"kid": 2}

    class MockEntryRepo(KnowledgeFsEntryRepository):
        async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
            return {"kid": 10, "knowledge_base_id": 2}

    class MockPropRepo(MetadataPropertyRepository):
        async def get_by_name(self, cursor, property_name):
            return {
                "kid": 5,
                "property_name": "status",
                "value_type": "string",
                "description": None,
                "ext_params": None,
            }

    class MockValueRepo(FileMetadataValueRepository):
        async def upsert_value(
            self,
            cursor,
            *,
            fs_entry_id,
            knowledge_base_id,
            property_def_id,
            value_type,
            value,
        ):
            return {"kid": 100}

        async def get_active_value(self, cursor, *, fs_entry_id, property_def_id):
            return None

        async def soft_delete_value(self, cursor, *, fs_entry_id, property_def_id):
            pass

    service, _ = _make_service(
        kb_repo=MockKBRepo(),
        entry_repo=MockEntryRepo(),
        prop_repo=MockPropRepo(),
        value_repo=MockValueRepo(),
    )

    request = UpdateFileMetadataRequest(
        kb_code="2",
        file_path="/docs/test.md",
        operation_list=[
            MetadataOperation(property_name="status", operation="append", value=["x"]),
        ],
    )

    with pytest.raises(KnowledgeBaseValidationError, match="not allowed"):
        await service.update_metadata(request)


@pytest.mark.asyncio
async def test_update_metadata_system_property_raises():
    """Modifying a system metadata property (is_system=True) should raise."""

    class MockKBRepo(KnowledgeBaseRepository):
        async def get_by_code(self, cursor, kb_code):
            return {"kid": 2}

    class MockEntryRepo(KnowledgeFsEntryRepository):
        async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
            return {"kid": 10, "knowledge_base_id": 2}

    class MockPropRepo(MetadataPropertyRepository):
        async def get_by_name(self, cursor, property_name):
            return {
                "kid": 5,
                "property_name": "fileName",
                "value_type": "string",
                "description": None,
                "ext_params": None,
                "is_system": True,
            }

    class MockValueRepo(FileMetadataValueRepository):
        async def upsert_value(
            self,
            cursor,
            *,
            fs_entry_id,
            knowledge_base_id,
            property_def_id,
            value_type,
            value,
        ):
            return {"kid": 100}

        async def get_active_value(self, cursor, *, fs_entry_id, property_def_id):
            return None

        async def soft_delete_value(self, cursor, *, fs_entry_id, property_def_id):
            pass

    service, _ = _make_service(
        kb_repo=MockKBRepo(),
        entry_repo=MockEntryRepo(),
        prop_repo=MockPropRepo(),
        value_repo=MockValueRepo(),
    )

    request = UpdateFileMetadataRequest(
        kb_code="2",
        file_path="/docs/test.md",
        operation_list=[
            MetadataOperation(
                property_name="fileName", operation="set", value="new.md"
            ),
        ],
    )

    with pytest.raises(
        KnowledgeBaseValidationError, match="cannot modify system metadata"
    ):
        await service.update_metadata(request)
