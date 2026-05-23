"""Unit tests for FileMetadataService."""

from __future__ import annotations

from typing import Any

import pytest

from by_qa.knowledge_base.api.metadata_schemas import (
    GetFileMetadataRequest,
    ListMetadataFieldsRequest,
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
from by_qa.knowledge_base.services.file_metadata_service import (
    FileMetadataService,
    _extract_system_fields,
    _extract_value,
)


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


def test_extract_value_preserves_null_for_string_list():
    row = {
        "value_type": "stringList",
        "value_string": None,
        "value_number": None,
        "value_boolean": None,
        "value_datetime": None,
        "value_string_list": None,
    }

    assert _extract_value(row) is None


# ---------------------------------------------------------------------------
# _extract_system_fields tests
# ---------------------------------------------------------------------------


def _make_entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "kid": 10,
        "knowledge_base_id": 1,
        "name": "readme.md",
        "entry_type": "FILE",
        "file_size": 2048,
        "mime_type": "text/markdown",
        "virtual_path": "/docs/readme.md",
        "created_at": None,
        "updated_at": None,
    }
    entry.update(overrides)
    return entry


def test_extract_system_fields_all_fields():
    entry = _make_entry()
    result = _extract_system_fields(entry)

    assert result["fileName"] == {"valueType": "string", "value": "readme.md"}
    assert result["fileType"] == {"valueType": "string", "value": "md"}
    assert result["fileSize"] == {"valueType": "number", "value": 2048}
    assert result["mimeType"] == {"valueType": "string", "value": "text/markdown"}
    assert result["filePath"] == {"valueType": "string", "value": "/docs/readme.md"}
    assert result["createdAt"]["valueType"] == "datetime"
    assert result["createdAt"]["value"] is None
    assert result["updatedAt"]["valueType"] == "datetime"
    assert result["updatedAt"]["value"] is None
    assert len(result) == 7


def test_extract_system_fields_with_filter():
    entry = _make_entry()
    result = _extract_system_fields(entry, property_names=["fileName", "fileSize"])

    assert len(result) == 2
    assert result["fileName"]["value"] == "readme.md"
    assert result["fileSize"]["value"] == 2048
    assert "fileType" not in result


def test_extract_system_fields_file_type_no_extension():
    entry = _make_entry(name="Makefile")
    result = _extract_system_fields(entry)

    assert result["fileType"]["value"] == ""


def test_extract_system_fields_file_type_multiple_dots():
    entry = _make_entry(name="archive.tar.gz")
    result = _extract_system_fields(entry)

    assert result["fileType"]["value"] == "gz"


def test_extract_system_fields_nullable_columns():
    entry = _make_entry(file_size=None, mime_type=None)
    result = _extract_system_fields(entry)

    assert result["fileSize"]["value"] is None
    assert result["mimeType"]["value"] is None


def test_extract_system_fields_empty_name_defaults():
    entry = _make_entry(name="")
    result = _extract_system_fields(entry)

    assert result["fileName"]["value"] == ""
    assert result["fileType"]["value"] == ""


# ---------------------------------------------------------------------------
# get_metadata system fields tests
# ---------------------------------------------------------------------------


class _MockKBRepo(KnowledgeBaseRepository):
    async def get_by_code(self, cursor, kb_code):
        return {"kid": 2}


class _MockEntryRepo(KnowledgeFsEntryRepository):
    def __init__(self, entry: dict[str, Any] | None = None):
        self._entry = entry

    async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
        return self._entry


class _MockValueRepo(FileMetadataValueRepository):
    def __init__(self, metadata_rows: list[dict[str, Any]] | None = None):
        self._metadata_rows = metadata_rows or []

    async def get_file_metadata(self, cursor, *, fs_entry_id, property_names=None):
        if property_names is None:
            return list(self._metadata_rows)
        return [r for r in self._metadata_rows if r["property_name"] in property_names]


@pytest.mark.asyncio
async def test_get_metadata_includes_system_fields():
    entry = _make_entry()
    service, _ = _make_service(
        kb_repo=_MockKBRepo(),
        entry_repo=_MockEntryRepo(entry),
        value_repo=_MockValueRepo(),
    )

    result = await service.get_metadata(
        GetFileMetadataRequest(kb_code="test", file_path="/docs/readme.md")
    )

    assert result["fileName"]["value"] == "readme.md"
    assert result["fileType"]["value"] == "md"
    assert result["fileSize"]["value"] == 2048
    assert result["mimeType"]["value"] == "text/markdown"
    assert result["filePath"]["value"] == "/docs/readme.md"


@pytest.mark.asyncio
async def test_get_metadata_merges_system_and_user_fields():
    entry = _make_entry()
    user_rows = [
        {
            "property_name": "status",
            "value_type": "string",
            "value_string": "published",
            "value_number": None,
            "value_boolean": None,
            "value_datetime": None,
            "value_string_list": None,
        },
    ]
    service, _ = _make_service(
        kb_repo=_MockKBRepo(),
        entry_repo=_MockEntryRepo(entry),
        value_repo=_MockValueRepo(user_rows),
    )

    result = await service.get_metadata(
        GetFileMetadataRequest(kb_code="test", file_path="/docs/readme.md")
    )

    assert result["fileName"]["value"] == "readme.md"
    assert result["status"] == {"valueType": "string", "value": "published"}
    assert len(result) == 8  # 7 system + 1 user


@pytest.mark.asyncio
async def test_get_metadata_field_list_filters_system_and_user_fields():
    entry = _make_entry()
    user_rows = [
        {
            "property_name": "status",
            "value_type": "string",
            "value_string": "draft",
            "value_number": None,
            "value_boolean": None,
            "value_datetime": None,
            "value_string_list": None,
        },
        {
            "property_name": "priority",
            "value_type": "number",
            "value_string": None,
            "value_number": 5,
            "value_boolean": None,
            "value_datetime": None,
            "value_string_list": None,
        },
    ]
    service, _ = _make_service(
        kb_repo=_MockKBRepo(),
        entry_repo=_MockEntryRepo(entry),
        value_repo=_MockValueRepo(user_rows),
    )

    result = await service.get_metadata(
        GetFileMetadataRequest(
            kb_code="test",
            file_path="/docs/readme.md",
            metadata_field_list=["fileName", "fileSize", "status"],
        )
    )

    assert len(result) == 3
    assert "fileName" in result
    assert "fileSize" in result
    assert "status" in result
    assert "fileType" not in result
    assert "priority" not in result


# ---------------------------------------------------------------------------
# list_metadata_fields system fields tests
# ---------------------------------------------------------------------------


class _MockValueRepoListProps(FileMetadataValueRepository):
    async def list_used_properties(self, cursor, *, knowledge_base_ids):
        return [
            {
                "property_name": "status",
                "value_type": "string",
                "description": "Document status",
            },
        ]


@pytest.mark.asyncio
async def test_list_metadata_fields_appends_system_fields():
    service, _ = _make_service(
        kb_repo=_MockKBRepo(),
        value_repo=_MockValueRepoListProps(),
    )

    from by_qa.knowledge_base.metadata_types import SYSTEM_FIELD_VALUE_TYPES

    result = await service.list_metadata_fields(
        ListMetadataFieldsRequest(kb_code_list=["test"])
    )

    assert len(result) == 8  # 1 user + 7 system

    # User property comes first
    assert result[0].property_name == "status"
    assert result[0].value_type == "string"

    # System fields come after, in defined order
    system_names = [r.property_name for r in result[1:]]
    expected = [
        "fileName",
        "fileType",
        "fileSize",
        "mimeType",
        "createdAt",
        "updatedAt",
        "filePath",
    ]
    assert system_names == expected

    for r in result[1:]:
        assert r.value_type == SYSTEM_FIELD_VALUE_TYPES[r.property_name]
        assert r.description is not None


@pytest.mark.asyncio
async def test_list_metadata_fields_no_user_properties():
    class _EmptyValueRepo(FileMetadataValueRepository):
        async def list_used_properties(self, cursor, *, knowledge_base_ids):
            return []

    service, _ = _make_service(
        kb_repo=_MockKBRepo(),
        value_repo=_EmptyValueRepo(),
    )

    result = await service.list_metadata_fields(
        ListMetadataFieldsRequest(kb_code_list=["test"])
    )

    assert len(result) == 7  # system fields only
    assert all(r.property_name for r in result)
    assert all(r.value_type for r in result)
