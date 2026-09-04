"""Tests for legacy-compatible and durable file-build task persistence."""

from __future__ import annotations

from pathlib import Path

from by_qa.knowledge_base.repositories.knowledge_build_task_repository import (
    KnowledgeBuildTaskRepository,
)

SQL_DIR = Path(__file__).resolve().parents[3] / "src/by_qa/knowledge_base/sql"


class FakeCursor:
    def __init__(self, *, fetchone_results=None, fetchall_results=None):
        self.executed: list[tuple[str, dict | None]] = []
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


async def test_get_latest_build_tasks_batches_file_entries():
    repo = KnowledgeBuildTaskRepository()
    expected = [
        {"fs_entry_id": 11, "status": "failed", "current_step": "markdown"},
        {"fs_entry_id": 12, "status": "complete", "current_step": "complete"},
    ]
    cursor = FakeCursor(fetchall_results=[expected])

    rows = await repo.get_latest_by_fs_entry_ids(cursor, fs_entry_ids=[11, 12])

    assert rows == expected
    sql, params = cursor.executed[0]
    assert "row_number() over" in sql.lower()
    assert "partition by fs_entry_id" in sql.lower()
    assert "order by created_at desc, kid desc" in sql.lower()
    assert params == {"fs_entry_ids": [11, 12]}


async def test_get_latest_build_tasks_skips_empty_input():
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor()

    assert await repo.get_latest_by_fs_entry_ids(cursor, fs_entry_ids=[]) == []
    assert cursor.executed == []


async def test_get_latest_current_build_task_matches_file_checksum_and_delete_state():
    repo = KnowledgeBuildTaskRepository()
    expected = {"kid": 21, "fs_entry_id": 11, "status": "succeeded"}
    cursor = FakeCursor(fetchone_results=[expected])

    row = await repo.get_latest_current_by_fs_entry_id(cursor, fs_entry_id=11)

    assert row == expected
    sql, params = cursor.executed[0]
    normalized_sql = " ".join(sql.lower().split())
    assert "join knowledge_fs_entry fs on fs.kid = task.fs_entry_id" in normalized_sql
    assert "task.input_checksum = fs.checksum" in normalized_sql
    assert "task.input_is_deleted = fs.is_deleted" in normalized_sql
    assert params == {"fs_entry_id": 11}


async def test_get_latest_current_build_tasks_batch_and_skip_empty_input():
    repo = KnowledgeBuildTaskRepository()
    expected = [
        {"kid": 21, "fs_entry_id": 11, "status": "succeeded"},
        {"kid": 22, "fs_entry_id": 12, "status": "running"},
    ]
    cursor = FakeCursor(fetchall_results=[expected])

    rows = await repo.get_latest_current_by_fs_entry_ids(cursor, fs_entry_ids=[11, 12])

    assert rows == expected
    sql, params = cursor.executed[0]
    normalized_sql = " ".join(sql.lower().split())
    assert "partition by task.fs_entry_id" in normalized_sql
    assert "task.input_checksum = fs.checksum" in normalized_sql
    assert "task.input_is_deleted = fs.is_deleted" in normalized_sql
    assert params == {"fs_entry_ids": [11, 12]}

    empty_cursor = FakeCursor()
    assert (
        await repo.get_latest_current_by_fs_entry_ids(empty_cursor, fs_entry_ids=[])
        == []
    )
    assert empty_cursor.executed == []


async def test_build_task_repository_keeps_the_file_build_schema_contract():
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 1}, {"kid": 2}])

    await repo.get_latest_by_fs_entry_id(cursor, fs_entry_id=11)
    await repo.create_task(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        status="running",
        current_step="markdown",
    )
    await repo.update_task(
        cursor,
        task_id=2,
        status="complete",
        current_step="complete",
        finished=True,
    )
    await repo.delete_for_fs_entry_id(cursor, fs_entry_id=11)

    for sql, params in cursor.executed:
        assert "task_type" not in sql
        assert "task_type" not in (params or {})
        assert "knowledge_semantic_processing_task" not in sql


def test_historical_build_task_sql_has_no_semantic_task_extensions():
    schema = (SQL_DIR / "006_knowledge_build_task.sql").read_text(encoding="utf-8")
    indexes = (SQL_DIR / "013_knowledge_build_task_indexes.sql").read_text(
        encoding="utf-8"
    )

    for new_field in (
        "task_type",
        "batch_id",
        "progress",
        "input_fingerprint",
        "definition_version",
        "request_params",
        "result_payload",
    ):
        assert new_field not in schema
    assert "task_type" not in indexes
    assert "knowledge_semantic_processing_task" not in schema
    assert "knowledge_semantic_processing_task" not in indexes


async def test_create_background_task_persists_stable_input_and_profile():
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 31}])

    row = await repo.create_background_task(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        batch_id="fb-31",
        file_path_snapshot="/docs/a.pdf",
        input_checksum="sha256:a",
        input_is_deleted=False,
        build_profile={"profileVersion": 1, "embedding": {"model": "m"}},
        build_profile_hash="a" * 64,
        priority=100,
    )

    assert row == {"kid": 31}
    sql, params = cursor.executed[0]
    assert "INSERT INTO knowledge_build_task" in sql
    assert params["batch_id"] == "fb-31"
    assert params["status"] == "pending"
    assert params["current_stage"] == "accepted"
    assert params["current_step"] == "markdown"
    assert params["input_checksum"] == "sha256:a"
    assert params["build_profile_hash"] == "a" * 64
    assert params["priority"] == 100


async def test_create_inline_task_requires_entity_origin_and_parent():
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 32}])

    row = await repo.create_inline_task(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        origin="ENTITY_DISCOVERY",
        parent_semantic_task_id=99,
        file_path_snapshot="/KnowledgeEntity/a.md",
        input_checksum="sha256:b",
        input_is_deleted=False,
        build_profile={"profileVersion": 1},
        build_profile_hash="b" * 64,
    )

    assert row == {"kid": 32}
    _, params = cursor.executed[0]
    assert params["execution_mode"] == "INLINE"
    assert params["origin"] == "ENTITY_DISCOVERY"
    assert params["parent_semantic_task_id"] == 99
    assert params["batch_id"] is None
    assert params["status"] == "running"


async def test_find_reusable_task_uses_only_stable_identity_fields():
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 44}])

    row = await repo.find_reusable_task(
        cursor,
        fs_entry_id=11,
        input_checksum="sha256:c",
        input_is_deleted=False,
        build_profile_hash="c" * 64,
        statuses=["PENDING", "SUCCEEDED"],
    )

    assert row == {"kid": 44}
    sql, params = cursor.executed[0]
    assert "file_path" not in sql.lower()
    assert "name" not in sql.lower()
    assert params == {
        "fs_entry_id": 11,
        "input_checksum": "sha256:c",
        "input_is_deleted": False,
        "build_profile_hash": "c" * 64,
        "statuses": ["pending", "succeeded"],
    }


async def test_supersede_active_task_revokes_lease_and_returns_row():
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 45, "status": "skipped"}])

    row = await repo.supersede_active_task(cursor, task_id=45)

    assert row == {"kid": 45, "status": "skipped"}
    sql, params = cursor.executed[0]
    assert "error_code = 'SUPERSEDED'" in sql
    assert "lease_token = NULL" in sql
    assert "status IN ('pending', 'running')" in sql
    assert params == {"task_id": 45}
