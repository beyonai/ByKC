"""Unit tests for KnowledgeFileReferenceRepository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from by_qa.knowledge_base.repositories.knowledge_file_reference_repository import (
    KnowledgeFileReferenceRepository,
)

MIGRATION_PATH = Path("src/by_qa/knowledge_base/sql/026_knowledge_file_reference.sql")


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
