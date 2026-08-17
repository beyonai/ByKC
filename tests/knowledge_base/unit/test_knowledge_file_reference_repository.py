"""Unit tests for KnowledgeFileReferenceRepository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from by_qa.knowledge_base.repositories.knowledge_file_reference_repository import (
    KnowledgeFileReferenceRepository,
)

MIGRATION_PATH = Path("src/by_qa/knowledge_base/sql/026_knowledge_file_reference.sql")
EXTENSION_MIGRATION_PATH = Path(
    "src/by_qa/knowledge_base/sql/030_knowledge_file_reference_semantic_extension.sql"
)


class FakeCursor:
    def __init__(
        self,
        *,
        fetchone_results: list[dict[str, Any] | None] | None = None,
        fetchall_results: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.executed.append((sql, params))

    async def fetchone(self) -> dict[str, Any] | None:
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    async def fetchall(self) -> list[dict[str, Any]]:
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []


@pytest.mark.asyncio
async def test_create_reference_inserts_resolved_unresolved_and_broken_rows():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {"kid": 11, "status": "resolved"},
            {"kid": 12, "status": "unresolved"},
            {"kid": 13, "status": "broken"},
        ]
    )

    resolved = await repo.create_reference(
        cursor,
        knowledge_base_id=1,
        source_fs_entry_id=2,
        target_fs_entry_id=3,
        original_target="../target.md",
        target_path=None,
        target_suffix="#section",
        status="resolved",
    )
    unresolved = await repo.create_reference(
        cursor,
        knowledge_base_id=1,
        source_fs_entry_id=2,
        target_fs_entry_id=None,
        original_target="./missing.md",
        target_path="/docs/missing.md",
        status="unresolved",
    )
    broken = await repo.create_reference(
        cursor,
        knowledge_base_id=1,
        source_fs_entry_id=2,
        target_fs_entry_id=None,
        original_target="./deleted.md",
        target_path="/docs/deleted.md",
        status="broken",
    )

    assert resolved["kid"] == 11
    assert unresolved["kid"] == 12
    assert broken["kid"] == 13
    assert len(cursor.executed) == 3
    for sql, params in cursor.executed:
        assert "INSERT INTO knowledge_file_reference" in sql
        assert "'MARKDOWN'" in sql
        assert "target_kind" in sql
        assert "RETURNING" in sql
        assert params["target_kind"] == "FILE"
    assert cursor.executed[0][1]["target_fs_entry_id"] == 3
    assert cursor.executed[0][1]["target_path"] is None
    assert cursor.executed[0][1]["target_suffix"] == "#section"
    assert cursor.executed[1][1]["target_fs_entry_id"] is None
    assert cursor.executed[1][1]["target_path"] == "/docs/missing.md"
    assert cursor.executed[1][1]["target_suffix"] == ""
    assert cursor.executed[2][1]["status"] == "broken"


@pytest.mark.asyncio
async def test_list_by_reference_ids_joins_target_and_exposes_deletion_state():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "kid": 11,
                    "target_fs_entry_id": 3,
                    "target_path": None,
                    "target_virtual_path": "/docs/target.md",
                    "target_is_deleted": True,
                }
            ]
        ]
    )

    rows = await repo.list_by_reference_ids(cursor, reference_ids=[11, 12])

    assert rows[0]["target_is_deleted"] is True
    assert rows[0]["target_virtual_path"] == "/docs/target.md"
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert (
        "LEFT JOIN knowledge_fs_entry target ON target.kid = kfr.target_fs_entry_id"
        in normalized
    )
    assert "target.is_deleted AS target_is_deleted" in normalized
    assert params == {"reference_ids": [11, 12]}
    assert "kfr.reference_type = 'MARKDOWN'" in normalized


@pytest.mark.asyncio
async def test_resolve_pending_for_path_updates_unresolved_and_broken_rows_by_exact_path():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {"kid": 21, "status": "resolved", "target_fs_entry_id": 7},
                {"kid": 22, "status": "resolved", "target_fs_entry_id": 7},
            ]
        ]
    )

    rows = await repo.resolve_pending_for_path(
        cursor,
        knowledge_base_id=1,
        target_path="/docs/restored.md",
        target_fs_entry_id=7,
    )

    assert [row["kid"] for row in rows] == [21, 22]
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert "status = 'resolved'" in normalized
    assert "target_path = NULL" in normalized
    assert "last_resolved_at = NOW()" in normalized
    assert "status IN ('unresolved', 'broken')" in normalized
    assert "reference_type = 'MARKDOWN'" in normalized
    assert "target_path = %(target_path)s" in normalized
    assert params == {
        "knowledge_base_id": 1,
        "target_path": "/docs/restored.md",
        "target_fs_entry_id": 7,
    }


@pytest.mark.asyncio
async def test_rebind_deleted_target_for_path_updates_resolved_rows_by_deleted_target_path():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {"kid": 23, "status": "resolved", "target_fs_entry_id": 9},
            ]
        ]
    )

    rows = await repo.rebind_deleted_target_for_path(
        cursor,
        knowledge_base_id=1,
        target_path="/docs/restored.md",
        target_fs_entry_id=9,
    )

    assert rows == [{"kid": 23, "status": "resolved", "target_fs_entry_id": 9}]
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert "FROM knowledge_fs_entry deleted_target" in normalized
    assert "deleted_target.kid = kfr.target_fs_entry_id" in normalized
    assert "deleted_target.is_deleted = TRUE" in normalized
    assert "deleted_target.virtual_path = %(target_path)s" in normalized
    assert "kfr.status = 'resolved'" in normalized
    assert "kfr.reference_type = 'MARKDOWN'" in normalized
    assert "kfr.target_fs_entry_id <> %(target_fs_entry_id)s" in normalized
    assert "target_path = NULL" in normalized
    assert params == {
        "knowledge_base_id": 1,
        "target_path": "/docs/restored.md",
        "target_fs_entry_id": 9,
    }


@pytest.mark.asyncio
async def test_mark_targets_deleted_writes_each_rows_own_target_path():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {"kid": 31, "status": "broken", "target_path": "/docs/a.md"},
                {"kid": 32, "status": "broken", "target_path": "/docs/b.md"},
            ]
        ]
    )

    rows = await repo.mark_targets_deleted(
        cursor,
        knowledge_base_id=1,
        targets=[
            (7, "/docs/a.md"),
            (8, "/docs/b.md"),
        ],
    )

    assert [row["target_path"] for row in rows] == ["/docs/a.md", "/docs/b.md"]
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert "FROM (VALUES" in normalized
    assert "target_path = deleted_targets.target_path" in normalized
    assert "target_fs_entry_id = NULL" in normalized
    assert "status = 'broken'" in normalized
    assert "kfr.reference_type = 'MARKDOWN'" in normalized
    assert params == {
        "knowledge_base_id": 1,
        "target_0_id": 7,
        "target_0_path": "/docs/a.md",
        "target_1_id": 8,
        "target_1_path": "/docs/b.md",
    }


@pytest.mark.asyncio
async def test_list_sources_by_target_supports_resolved_and_broken_lookup():
    repo = KnowledgeFileReferenceRepository()

    resolved_cursor = FakeCursor(fetchall_results=[[{"kid": 41}]])
    resolved_rows = await repo.list_sources_by_target(
        resolved_cursor,
        knowledge_base_id=1,
        target_fs_entry_id=7,
    )
    assert resolved_rows == [{"kid": 41}]
    resolved_sql, resolved_params = resolved_cursor.executed[0]
    assert "target_fs_entry_id = %(target_fs_entry_id)s" in resolved_sql
    assert "status = 'resolved'" in resolved_sql
    assert "kfr.reference_type = 'MARKDOWN'" in resolved_sql
    assert resolved_params == {"knowledge_base_id": 1, "target_fs_entry_id": 7}

    broken_cursor = FakeCursor(fetchall_results=[[{"kid": 42}]])
    broken_rows = await repo.list_sources_by_target(
        broken_cursor,
        knowledge_base_id=1,
        target_path="/docs/deleted.md",
    )
    assert broken_rows == [{"kid": 42}]
    broken_sql, broken_params = broken_cursor.executed[0]
    assert "target_path = %(target_path)s" in broken_sql
    assert "status IN ('unresolved', 'broken')" in broken_sql
    assert "kfr.reference_type = 'MARKDOWN'" in broken_sql
    assert broken_params == {"knowledge_base_id": 1, "target_path": "/docs/deleted.md"}


def test_reference_migration_declares_delete_and_state_constraints():
    sql = " ".join(MIGRATION_PATH.read_text(encoding="utf-8").split())

    assert (
        "knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) "
        "ON DELETE CASCADE"
    ) in sql
    assert (
        "source_fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) "
        "ON DELETE CASCADE"
    ) in sql
    assert (
        "target_fs_entry_id bigint NULL REFERENCES knowledge_fs_entry(kid) "
        "ON DELETE RESTRICT"
    ) in sql
    assert "CONSTRAINT chk_knowledge_file_reference_state CHECK" in sql
    assert "status = 'resolved'" in sql
    assert "target_fs_entry_id IS NOT NULL" in sql
    assert "target_path IS NULL" in sql
    assert "status IN ('unresolved', 'broken')" in sql
    assert "target_fs_entry_id IS NULL" in sql
    assert "target_path IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_delete_and_list_markdown_by_source_never_touch_semantic_rows():
    repo = KnowledgeFileReferenceRepository()
    delete_cursor = FakeCursor()
    list_cursor = FakeCursor(fetchall_results=[[{"kid": 1}]])

    await repo.delete_for_source_fs_entry_id(delete_cursor, source_fs_entry_id=9)
    rows = await repo.list_by_source(list_cursor, source_fs_entry_id=9)

    assert rows == [{"kid": 1}]
    delete_sql, _ = delete_cursor.executed[0]
    list_sql, _ = list_cursor.executed[0]
    assert "reference_type = 'MARKDOWN'" in delete_sql
    assert "kfr.reference_type = 'MARKDOWN'" in list_sql


@pytest.mark.asyncio
async def test_mark_target_restored_only_updates_markdown_rows():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor(fetchall_results=[[]])

    await repo.mark_target_restored(
        cursor,
        knowledge_base_id=4,
        target_path="/docs/restored.md",
        target_fs_entry_id=8,
    )

    sql, _ = cursor.executed[0]
    assert "reference_type = 'MARKDOWN'" in sql


@pytest.mark.asyncio
async def test_upsert_semantic_relation_uses_stable_partial_unique_key():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 51,
                "reference_type": "SEMANTIC",
                "relation_code": "MENTIONS",
            }
        ]
    )

    row = await repo.upsert_semantic_relation(
        cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        target_fs_entry_id=12,
        relation_code="MENTIONS",
        original_target="/KnowledgeEntity/Foo.md",
        confidence=0.98,
        discovered_by="AC_EXACT",
        definition_version="v1",
        source_task_id=99,
    )

    assert row == {
        "kid": 51,
        "reference_type": "SEMANTIC",
        "relation_code": "MENTIONS",
    }
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert "'SEMANTIC'" in normalized
    assert "'resolved'" in normalized
    assert "target.knowledge_base_id = %(knowledge_base_id)s" in normalized
    assert "source.knowledge_base_id = %(knowledge_base_id)s" in normalized
    assert "ON DUPLICATE KEY UPDATE" in normalized
    assert params == {
        "knowledge_base_id": 3,
        "source_fs_entry_id": 11,
        "target_fs_entry_id": 12,
        "relation_code": "MENTIONS",
        "original_target": "/KnowledgeEntity/Foo.md",
        "confidence": 0.98,
        "discovered_by": "AC_EXACT",
        "definition_version": "v1",
        "source_task_id": 99,
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "source_fs_entry_id": 1,
                "target_fs_entry_id": 1,
                "relation_code": "MENTIONS",
                "confidence": 1.0,
            },
            "source and target must differ",
        ),
        (
            {
                "source_fs_entry_id": 1,
                "target_fs_entry_id": 2,
                "relation_code": "UNKNOWN",
                "confidence": 1.0,
            },
            "relation_code must be one of",
        ),
        (
            {
                "source_fs_entry_id": 1,
                "target_fs_entry_id": 2,
                "relation_code": "MENTIONS",
                "confidence": 1.1,
            },
            "confidence must be between",
        ),
    ],
)
@pytest.mark.asyncio
async def test_upsert_semantic_relation_validates_identity_and_enums(kwargs, message):
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor()

    with pytest.raises(ValueError, match=message):
        await repo.upsert_semantic_relation(
            cursor,
            knowledge_base_id=3,
            original_target="/KnowledgeEntity/Foo.md",
            **kwargs,
        )

    assert cursor.executed == []


@pytest.mark.asyncio
async def test_list_and_count_semantic_by_source_share_filters_and_pagination():
    repo = KnowledgeFileReferenceRepository()
    list_cursor = FakeCursor(
        fetchall_results=[
            [
                {
                    "kid": 51,
                    "source_virtual_path": "/docs/source.md",
                    "target_virtual_path": "/KnowledgeEntity/Foo.md",
                    "source_is_deleted": False,
                    "target_is_deleted": False,
                }
            ]
        ]
    )
    count_cursor = FakeCursor(fetchone_results=[{"total": 4}])

    rows = await repo.list_semantic_by_source(
        list_cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        relation_code=["MENTIONS", "DEPENDS_ON"],
        limit=20,
        offset=40,
    )
    total = await repo.count_semantic_by_source(
        count_cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        relation_code=["MENTIONS", "DEPENDS_ON"],
    )

    assert total == 4
    assert rows[0]["target_virtual_path"] == "/KnowledgeEntity/Foo.md"
    list_sql, list_params = list_cursor.executed[0]
    count_sql, count_params = count_cursor.executed[0]
    for sql in (list_sql, count_sql):
        assert "kfr.reference_type = 'SEMANTIC'" in sql
        assert "kfr.relation_code = ANY(%(relation_codes)s)" in sql
        assert "source.is_deleted = FALSE" in sql
        assert "target.is_deleted = FALSE" in sql
    assert "source.virtual_path AS source_virtual_path" in list_sql
    assert "target.virtual_path AS target_virtual_path" in list_sql
    assert "LIMIT %(limit)s OFFSET %(offset)s" in list_sql
    assert list_params["relation_codes"] == ["MENTIONS", "DEPENDS_ON"]
    assert list_params["limit"] == 20
    assert list_params["offset"] == 40
    assert count_params["relation_codes"] == ["MENTIONS", "DEPENDS_ON"]


@pytest.mark.asyncio
async def test_list_and_count_semantic_by_target_support_single_relation_code():
    repo = KnowledgeFileReferenceRepository()
    list_cursor = FakeCursor(fetchall_results=[[{"kid": 61}]])
    count_cursor = FakeCursor(fetchone_results=[{"total": 1}])

    rows = await repo.list_semantic_by_target(
        list_cursor,
        knowledge_base_id=3,
        target_fs_entry_id=12,
        relation_code="IS_A",
        include_deleted_entries=True,
    )
    total = await repo.count_semantic_by_target(
        count_cursor,
        knowledge_base_id=3,
        target_fs_entry_id=12,
        relation_code="IS_A",
        include_deleted_entries=True,
    )

    assert rows == [{"kid": 61}]
    assert total == 1
    for cursor in (list_cursor, count_cursor):
        sql, params = cursor.executed[0]
        assert "kfr.target_fs_entry_id = %(target_fs_entry_id)s" in sql
        assert "source.is_deleted = FALSE" not in sql
        assert params["relation_codes"] == ["IS_A"]


@pytest.mark.asyncio
async def test_semantic_cleanup_is_scoped_by_source_or_task():
    repo = KnowledgeFileReferenceRepository()
    source_cursor = FakeCursor(fetchall_results=[[{"kid": 71}]])
    task_cursor = FakeCursor(fetchall_results=[[{"kid": 72}]])

    source_rows = await repo.delete_semantic_for_source_fs_entry_id(
        source_cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        relation_code="MENTIONS",
    )
    task_rows = await repo.delete_semantic_for_source_task_id(
        task_cursor,
        knowledge_base_id=3,
        source_task_id=99,
    )

    assert source_rows == [{"kid": 71}]
    assert task_rows == [{"kid": 72}]
    source_sql, source_params = source_cursor.executed[0]
    task_sql, task_params = task_cursor.executed[0]
    assert "reference_type = 'SEMANTIC'" in source_sql
    assert "relation_code = ANY(%(relation_codes)s)" in source_sql
    assert source_params["relation_codes"] == ["MENTIONS"]
    assert "reference_type = 'SEMANTIC'" in task_sql
    assert "source_task_id = %(source_task_id)s" in task_sql
    assert task_params == {"knowledge_base_id": 3, "source_task_id": 99}


def test_semantic_schema_and_upgrade_define_columns_constraints_and_indexes():
    historical_sql = " ".join(MIGRATION_PATH.read_text(encoding="utf-8").split())
    upgrade_sql = " ".join(EXTENSION_MIGRATION_PATH.read_text(encoding="utf-8").split())

    for extension_name in (
        "reference_type",
        "relation_code",
        "confidence",
        "discovered_by",
        "definition_version",
        "source_task_id",
    ):
        assert extension_name not in historical_sql

    assert "reference_type varchar(16)" in upgrade_sql
    assert "relation_code varchar(32)" in upgrade_sql
    assert "confidence numeric(5,4)" in upgrade_sql
    assert "discovered_by varchar(32)" in upgrade_sql
    assert "definition_version varchar(64)" in upgrade_sql
    assert "source_task_id bigint" in upgrade_sql
    assert "REFERENCES knowledge_semantic_processing_task(kid)" in upgrade_sql
    assert "REFERENCES knowledge_build_task(kid)" not in upgrade_sql
    assert "ON DELETE SET NULL" in upgrade_sql
    assert "chk_knowledge_file_reference_type" in upgrade_sql
    assert "chk_knowledge_file_reference_semantic_state" in upgrade_sql
    assert "source_fs_entry_id <> target_fs_entry_id" in upgrade_sql
    assert "uq_kfr_semantic_relation" in upgrade_sql
    assert "idx_kfr_semantic_source" in upgrade_sql
    assert "idx_kfr_semantic_target" in upgrade_sql
    assert "idx_kfr_semantic_source_task" in upgrade_sql
    assert "knowledge_document_relation_evidence" not in upgrade_sql
    assert "DEFAULT 'MARKDOWN'" in upgrade_sql
    assert "information_schema.columns" in upgrade_sql
    assert "pg_constraint" in upgrade_sql
