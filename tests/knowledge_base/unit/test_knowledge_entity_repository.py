"""Unit tests for KnowledgeEntity metadata reads."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from by_qa.knowledge_base.repositories.knowledge_entity_asset_repository import (
    KnowledgeEntityAssetRepository,
)
from by_qa.knowledge_base.repositories.knowledge_entity_repository import (
    KnowledgeEntityRepository,
)


class FakeCursor:
    def __init__(self, *, fetchall_results=None, fetchone_results=None):
        self.executed: list[tuple[str, dict | None]] = []
        self._fetchall_results = list(fetchall_results or [])
        self._fetchone_results = list(fetchone_results or [])

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []

    async def fetchone(self):
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None


async def test_list_topics_can_filter_by_incremental_watermark():
    watermark = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "kid": 41,
                    "entity_name": "New topic",
                    "normalized_entity_name": "new topic",
                    "created_at": watermark,
                    "updated_at": watermark,
                }
            ]
        ]
    )
    repository = KnowledgeEntityAssetRepository("knowledge_entity_embedding_test")

    rows = await repository.list_topics_for_entity_file(
        cursor,
        knowledge_base_id=1,
        fs_entry_id=30,
        updated_after=watermark,
    )

    sql, params = cursor.executed[0]
    assert "topic.updated_at > %(updated_after)s" in sql
    assert params["updated_after"] == watermark
    assert rows[0]["entity_name"] == "New topic"


def file_row(
    *,
    kid: int = 10,
    knowledge_base_id: int = 7,
    file_path: str = "/docs/a.md",
    property_name: str | None = None,
    value_type: str | None = None,
    value_string=None,
    value_string_list=None,
    value_boolean=None,
):
    return {
        "kid": kid,
        "knowledge_base_id": knowledge_base_id,
        "name": file_path.rsplit("/", maxsplit=1)[-1],
        "file_path": file_path,
        "checksum": "sha-1",
        "file_bucket_name": "originals",
        "file_object_key": f"{kid}/original",
        "markdown_bucket_name": "markdown",
        "markdown_object_key": f"{kid}/document.md",
        "mime_type": "text/markdown",
        "line_count": 20,
        "updated_at": datetime(2026, 8, 17, tzinfo=UTC),
        "metadata_value_id": None if property_name is None else 100 + kid,
        "property_name": property_name,
        "value_type": value_type,
        "value_string": value_string,
        "value_number": None,
        "value_boolean": value_boolean,
        "value_datetime": None,
        "value_string_list": value_string_list,
    }


async def test_get_file_with_metadata_folds_eav_values_and_storage_fields():
    cursor = FakeCursor(
        fetchall_results=[
            [
                file_row(
                    property_name="documentKind",
                    value_type="string",
                    value_string="knowledgeEntity",
                ),
                file_row(
                    property_name="processingCapabilities",
                    value_type="stringList",
                    value_string_list='["entityEnrich"]',
                ),
                file_row(
                    property_name="entityName",
                    value_type="string",
                    value_string="OSOT-OCG",
                ),
                file_row(
                    property_name="aliases",
                    value_type="stringList",
                    value_string_list=["OSOT", "OCG", 7],
                ),
                file_row(
                    property_name="subjectFileId",
                    value_type="string",
                    value_string="200",
                ),
                file_row(
                    property_name="entityType",
                    value_type="string",
                    value_string="system",
                ),
                file_row(
                    property_name="entityEnriched",
                    value_type="boolean",
                    value_boolean=True,
                ),
            ]
        ]
    )

    result = await KnowledgeEntityRepository().get_file_with_metadata(
        cursor,
        knowledge_base_id=7,
        file_path="docs/a.md",
    )

    assert result == {
        "kid": 10,
        "knowledge_base_id": 7,
        "name": "a.md",
        "file_path": "/docs/a.md",
        "checksum": "sha-1",
        "file_bucket_name": "originals",
        "file_object_key": "10/original",
        "markdown_bucket_name": "markdown",
        "markdown_object_key": "10/document.md",
        "mime_type": "text/markdown",
        "line_count": 20,
        "updated_at": datetime(2026, 8, 17, tzinfo=UTC),
        "document_kind": "knowledgeEntity",
        "document_kind_configured": True,
        "processing_capabilities": ["entityEnrich"],
        "processing_capabilities_configured": True,
        "entity_name": "OSOT-OCG",
        "aliases": ["OSOT", "OCG"],
        "subject_file_id": "200",
        "entity_type": "system",
        "entity_enriched": True,
    }
    sql, params = cursor.executed[0]
    assert "fe.virtual_path = %(file_path)s" in sql
    assert "mv.property_name = ANY(%(property_names)s)" in sql
    assert params["file_path"] == "/docs/a.md"
    assert "entityName" in params["property_names"]
    assert "entityEnriched" in params["property_names"]
    assert "definitionVersion" not in params["property_names"]


async def test_list_files_with_metadata_uses_segment_safe_prefix_and_stable_order():
    rows = [
        file_row(
            kid=10,
            file_path="/docs/a.md",
            property_name="documentKind",
            value_type="string",
            value_string="original",
        ),
        file_row(
            kid=11,
            file_path="/docs/b.md",
            property_name="documentKind",
            value_type="string",
            value_string="original",
        ),
    ]
    cursor = FakeCursor(fetchall_results=[rows])

    result = await KnowledgeEntityRepository().list_files_with_metadata(
        cursor,
        knowledge_base_id=7,
        path_prefix="/docs/",
    )

    assert [item["kid"] for item in result] == [10, 11]
    sql, params = cursor.executed[0]
    assert "LEFT(" in sql
    assert "%(path_prefix)s || '/'" in sql
    assert "ORDER BY fe.virtual_path ASC, fe.kid ASC" in sql
    assert params["path_prefix"] == "/docs"
    assert params["knowledge_base_id"] == 7


async def test_list_files_without_prefix_keeps_query_parameterized():
    cursor = FakeCursor(fetchall_results=[[]])

    await KnowledgeEntityRepository().list_files_with_metadata(
        cursor,
        knowledge_base_id=7,
    )

    sql, params = cursor.executed[0]
    assert "%(path_prefix)s::text IS NULL" in sql
    assert params["path_prefix"] is None
    assert "7" not in sql


async def test_missing_processing_capabilities_is_distinct_from_explicit_empty_list():
    cursor = FakeCursor(
        fetchall_results=[
            [file_row(kid=10, file_path="/missing.md")],
            [
                file_row(
                    kid=11,
                    file_path="/disabled.md",
                    property_name="processingCapabilities",
                    value_type="stringList",
                    value_string_list=[],
                )
            ],
        ]
    )
    repo = KnowledgeEntityRepository()

    missing = await repo.list_files_with_metadata(
        cursor,
        knowledge_base_id=7,
        path_prefix="/missing.md",
    )
    disabled = await repo.list_files_with_metadata(
        cursor,
        knowledge_base_id=7,
        path_prefix="/disabled.md",
    )

    assert missing[0]["processing_capabilities"] == []
    assert missing[0]["processing_capabilities_configured"] is False
    assert disabled[0]["processing_capabilities"] == []
    assert disabled[0]["processing_capabilities_configured"] is True


async def test_missing_document_kind_is_distinct_from_explicit_empty_value():
    cursor = FakeCursor(
        fetchall_results=[
            [file_row(kid=10, file_path="/missing.md")],
            [
                file_row(
                    kid=11,
                    file_path="/invalid.md",
                    property_name="documentKind",
                    value_type="string",
                    value_string="",
                )
            ],
        ]
    )
    repo = KnowledgeEntityRepository()

    missing = await repo.list_files_with_metadata(
        cursor, knowledge_base_id=7, path_prefix="/missing.md"
    )
    invalid = await repo.list_files_with_metadata(
        cursor, knowledge_base_id=7, path_prefix="/invalid.md"
    )

    assert missing[0]["document_kind"] is None
    assert missing[0]["document_kind_configured"] is False
    assert invalid[0]["document_kind"] == ""
    assert invalid[0]["document_kind_configured"] is True


async def test_list_entity_surfaces_supports_systemwide_and_same_kb_snapshots():
    system_rows = [
        file_row(
            kid=10,
            knowledge_base_id=7,
            file_path="/KnowledgeEntity/a.md",
            property_name="entityName",
            value_type="string",
            value_string="Alpha",
        ),
        file_row(
            kid=10,
            knowledge_base_id=7,
            file_path="/KnowledgeEntity/a.md",
            property_name="aliases",
            value_type="stringList",
            value_string_list='["A", "First"]',
        ),
        file_row(
            kid=20,
            knowledge_base_id=9,
            file_path="/KnowledgeEntity/b.md",
            property_name="entityName",
            value_type="string",
            value_string="Beta",
        ),
        file_row(
            kid=20,
            knowledge_base_id=9,
            file_path="/KnowledgeEntity/b.md",
            property_name="subjectFileId",
            value_type="string",
            value_string="21",
        ),
    ]
    cursor = FakeCursor(fetchall_results=[system_rows, system_rows[:2]])
    repo = KnowledgeEntityRepository()

    system_result = await repo.list_entity_surfaces(cursor)
    kb_result = await repo.list_entity_surfaces(cursor, knowledge_base_id=7)

    assert system_result == [
        {
            "kid": 10,
            "knowledge_base_id": 7,
            "name": "a.md",
            "file_path": "/KnowledgeEntity/a.md",
            "updated_at": datetime(2026, 8, 17, tzinfo=UTC),
            "entity_name": "Alpha",
            "aliases": ["A", "First"],
            "subject_file_id": None,
            "entity_type": None,
            "entity_enriched": None,
        },
        {
            "kid": 20,
            "knowledge_base_id": 9,
            "name": "b.md",
            "file_path": "/KnowledgeEntity/b.md",
            "updated_at": datetime(2026, 8, 17, tzinfo=UTC),
            "entity_name": "Beta",
            "aliases": [],
            "subject_file_id": "21",
            "entity_type": None,
            "entity_enriched": None,
        },
    ]
    assert [item["kid"] for item in kb_result] == [10]
    system_sql, system_params = cursor.executed[0]
    assert "document_kind.property_name = 'documentKind'" in system_sql
    assert "document_kind.value_string = 'knowledgeEntity'" in system_sql
    assert system_sql.count("SELECT") == 1
    assert "%(knowledge_base_id)s::bigint IS NULL" in system_sql
    assert system_params["knowledge_base_id"] is None
    assert cursor.executed[1][1]["knowledge_base_id"] == 7


async def test_get_files_by_ids_batches_metadata_and_empty_ids_skip_sql():
    cursor = FakeCursor(
        fetchall_results=[
            [
                file_row(kid=10, file_path="/a.md"),
                file_row(kid=20, file_path="/b.md"),
            ]
        ]
    )
    repo = KnowledgeEntityRepository()

    result = await repo.get_files_by_ids(
        cursor,
        knowledge_base_id=7,
        fs_entry_ids=[20, 10],
    )
    empty = await repo.get_files_by_ids(
        cursor,
        knowledge_base_id=7,
        fs_entry_ids=[],
    )

    assert [item["kid"] for item in result] == [10, 20]
    assert empty == []
    assert len(cursor.executed) == 1
    sql, params = cursor.executed[0]
    assert "fe.kid = ANY(%(fs_entry_ids)s)" in sql
    assert params["fs_entry_ids"] == [20, 10]
    assert "ORDER BY fe.kid ASC" in sql


async def test_latest_successful_enrich_watermark_is_scoped_before_current_task():
    finished_at = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    cursor = FakeCursor(fetchone_results=[{"finished_at": finished_at}])

    result = await KnowledgeEntityRepository().get_latest_successful_enrich_finished_at(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=20,
        before_task_id=99,
    )

    assert result == finished_at
    sql, params = cursor.executed[0]
    assert "task_type = 'DOCUMENT_ENRICH'" in sql
    assert "status = 'succeeded'" in sql
    assert "kid < %(before_task_id)s" in sql
    assert params == {
        "knowledge_base_id": 7,
        "fs_entry_id": 20,
        "before_task_id": 99,
    }


@pytest.mark.parametrize("path", ["", "   "])
async def test_empty_file_path_is_rejected(path):
    with pytest.raises(ValueError, match="path must not be empty"):
        await KnowledgeEntityRepository().get_file_with_metadata(
            FakeCursor(), knowledge_base_id=7, file_path=path
        )
