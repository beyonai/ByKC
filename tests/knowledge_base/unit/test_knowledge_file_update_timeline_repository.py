"""Unit tests for document-update timeline schema and persistence."""

from pathlib import Path

import pytest

from by_qa.knowledge_base.repositories.knowledge_file_update_timeline_repository import (
    KnowledgeFileUpdateTimelineRepository,
)


class FakeCursor:
    def __init__(self, rows=None):
        self.executed = []
        self.rows = list(rows or [])

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_timeline_migration_uses_entry_identity_and_constrains_values():
    sql = Path(
        "src/by_qa/knowledge_base/sql/027_knowledge_file_update_timeline.sql"
    ).read_text(encoding="utf-8")
    lowered = sql.lower()

    assert "knowledge_file_update_timeline" in lowered
    assert "file_path" not in lowered
    assert (
        "knowledge_base_id bigint not null references knowledge_base(kid) on delete cascade"
        in lowered
    )
    assert (
        "fs_entry_id bigint not null references knowledge_fs_entry(kid) on delete cascade"
        in lowered
    )
    assert "event_type text not null default 'update'" in lowered
    assert "check (event_type in ('update'))" in lowered
    assert "check (summary_source in ('rule_based', 'fixed', 'llm'))" in lowered
    assert (
        "on knowledge_file_update_timeline (fs_entry_id, created_at desc, kid desc)"
        in lowered
    )


@pytest.mark.asyncio
async def test_create_update_event_returns_inserted_timeline_row():
    repo = KnowledgeFileUpdateTimelineRepository()
    cursor = FakeCursor(rows=[{"kid": 31, "summary_source": "RULE_BASED"}])

    row = await repo.create_update_event(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=81,
        old_checksum="old",
        new_checksum="new",
        old_file_size=10,
        new_file_size=20,
        summary="File changed",
        summary_source="RULE_BASED",
    )

    assert row == {"kid": 31, "summary_source": "RULE_BASED"}
    sql, params = cursor.executed[-1]
    assert "INSERT INTO knowledge_file_update_timeline" in sql
    assert "RETURNING" in sql
    assert params == {
        "knowledge_base_id": 7,
        "fs_entry_id": 81,
        "old_checksum": "old",
        "new_checksum": "new",
        "old_file_size": 10,
        "new_file_size": 20,
        "summary": "File changed",
        "summary_source": "RULE_BASED",
    }


@pytest.mark.asyncio
async def test_update_summary_from_llm_only_targets_timeline_id():
    repo = KnowledgeFileUpdateTimelineRepository()
    cursor = FakeCursor(rows=[{"kid": 31, "summary_source": "LLM"}])

    row = await repo.update_summary_from_llm(cursor, timeline_id=31, summary="LLM")

    assert row == {"kid": 31, "summary_source": "LLM"}
    sql, params = cursor.executed[-1]
    assert "summary = %(summary)s" in sql
    assert "summary_source = 'LLM'" in sql
    assert "updated_at = NOW()" in sql
    assert "WHERE kid = %(timeline_id)s" in sql
    assert params == {"timeline_id": 31, "summary": "LLM"}
