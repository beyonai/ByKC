"""Unit tests for the isolated semantic-processing task repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from by_qa.knowledge_base.repositories.knowledge_semantic_processing_task_repository import (
    KnowledgeSemanticProcessingTaskRepository,
)

MIGRATION_PATH = Path(
    "src/by_qa/knowledge_base/sql/029_knowledge_semantic_processing_task.sql"
)


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


async def test_create_processing_task_persists_method_and_json_request():
    row = {"kid": 91, "task_type": "ENTITY_DISCOVERY"}
    cursor = FakeCursor(fetchone_results=[row])
    repo = KnowledgeSemanticProcessingTaskRepository()

    result = await repo.create_processing_task(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        task_type="entity_discovery",
        status="PENDING",
        batch_id="ed-1",
        current_stage="queued",
        progress=0,
        input_fingerprint="fp-1",
        input_checksum="sha-1",
        method_version="discovery/1.0",
        index_version="ac/18",
        request_params={"maxEntities": 12, "text": "中文"},
    )

    assert result == row
    sql, params = cursor.executed[0]
    assert "INSERT INTO knowledge_semantic_processing_task" in sql
    assert "knowledge_build_task" not in sql
    assert "%(request_params)s::jsonb" in sql
    assert sql.count("%(status)s::varchar(32)") == 3
    assert "'running'::varchar(32)" in sql
    assert params["task_type"] == "ENTITY_DISCOVERY"
    assert params["status"] == "pending"
    assert params["batch_id"] == "ed-1"
    assert params["input_checksum"] == "sha-1"
    assert json.loads(params["request_params"]) == {
        "maxEntities": 12,
        "text": "中文",
    }


async def test_create_processing_task_normalizes_explicit_null_progress():
    cursor = FakeCursor(fetchone_results=[{"kid": 91}])
    repo = KnowledgeSemanticProcessingTaskRepository()

    await repo.create_processing_task(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        task_type="DOCUMENT_ENRICH",
        progress=None,
    )

    assert cursor.executed[0][1]["progress"] == 0
    assert json.loads(cursor.executed[0][1]["request_params"]) == {}
    assert json.loads(cursor.executed[0][1]["extra_params"]) == {}


async def test_update_processing_task_is_scoped_to_task_type():
    row = {"kid": 91, "status": "succeeded"}
    cursor = FakeCursor(fetchone_results=[row])
    repo = KnowledgeSemanticProcessingTaskRepository()

    result = await repo.update_processing_task(
        cursor,
        task_id=91,
        task_type="DOCUMENT_ENRICH",
        status="SUCCEEDED",
        current_stage="persist",
        progress=100,
        result_payload={"warningCount": 2},
        started=True,
        finished=True,
    )

    assert result == row
    sql, params = cursor.executed[0]
    assert "UPDATE knowledge_semantic_processing_task" in sql
    assert "task_type = %(task_type)s" in sql
    assert params["task_type"] == "DOCUMENT_ENRICH"
    assert params["status"] == "succeeded"
    assert params["progress"] == 100
    assert json.loads(params["result_payload"]) == {"warningCount": 2}


async def test_list_processing_tasks_filters_and_groups_latest_before_status():
    rows = [{"kid": 91}, {"kid": 90}]
    cursor = FakeCursor(fetchall_results=[rows])
    repo = KnowledgeSemanticProcessingTaskRepository()

    result = await repo.list_processing_tasks(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        batch_id="ed-1",
        task_type="entity_discovery",
        statuses=["RUNNING", "PENDING"],
        latest_only=True,
        limit=20,
        offset=40,
    )

    assert result == rows
    sql, params = cursor.executed[0]
    assert "FROM knowledge_semantic_processing_task task" in sql
    assert "task.knowledge_base_id = %(knowledge_base_id)s" in sql
    assert "task.fs_entry_id = %(fs_entry_id)s" in sql
    assert "task.batch_id = %(batch_id)s" in sql
    assert "task.task_type = %(task_type)s" in sql
    assert "PARTITION BY task.fs_entry_id, task.task_type" in sql
    assert sql.index("ROW_NUMBER()") < sql.index("status = ANY(%(statuses)s)")
    assert params == {
        "knowledge_base_id": 7,
        "task_type": "ENTITY_DISCOVERY",
        "fs_entry_id": 11,
        "batch_id": "ed-1",
        "statuses": ["running", "pending"],
        "latest_only": True,
        "limit": 20,
        "offset": 40,
    }


async def test_list_processing_tasks_without_type_reads_only_semantic_table():
    cursor = FakeCursor()
    repo = KnowledgeSemanticProcessingTaskRepository()

    await repo.list_processing_tasks(cursor, knowledge_base_id=7)

    sql, params = cursor.executed[0]
    assert "knowledge_semantic_processing_task" in sql
    assert "knowledge_build_task" not in sql
    assert "task_type" not in params


async def test_count_processing_tasks_uses_same_grouping_and_filters():
    cursor = FakeCursor(fetchone_results=[{"total": 3}])
    repo = KnowledgeSemanticProcessingTaskRepository()

    result = await repo.count_processing_tasks(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        statuses=["FAILED"],
        latest_only=False,
    )

    assert result == 3
    sql, params = cursor.executed[0]
    assert "FROM knowledge_semantic_processing_task task" in sql
    assert "PARTITION BY task.fs_entry_id, task.task_type" in sql
    assert "task.fs_entry_id = %(fs_entry_id)s" in sql
    assert params["statuses"] == ["failed"]
    assert params["latest_only"] is False


@pytest.mark.parametrize("task_type", ["FILE_BUILD", "UNKNOWN"])
async def test_processing_task_methods_reject_non_semantic_types(task_type):
    repo = KnowledgeSemanticProcessingTaskRepository()
    cursor = FakeCursor()

    with pytest.raises(ValueError, match="unsupported semantic task type"):
        await repo.create_processing_task(
            cursor,
            knowledge_base_id=7,
            fs_entry_id=11,
            task_type=task_type,
        )


@pytest.mark.parametrize("progress", [-1, 101])
async def test_processing_task_methods_reject_invalid_progress(progress):
    repo = KnowledgeSemanticProcessingTaskRepository()
    cursor = FakeCursor()

    with pytest.raises(ValueError, match="progress must be between 0 and 100"):
        await repo.create_processing_task(
            cursor,
            knowledge_base_id=7,
            fs_entry_id=11,
            task_type="ENTITY_DISCOVERY",
            progress=progress,
        )


def test_additive_migration_creates_isolated_task_table_with_contracts():
    sql = " ".join(MIGRATION_PATH.read_text(encoding="utf-8").split())

    assert "CREATE TABLE IF NOT EXISTS knowledge_semantic_processing_batch" in sql
    assert "CREATE TABLE IF NOT EXISTS knowledge_semantic_processing_task" in sql
    assert "ALTER TABLE knowledge_build_task" in sql
    assert "parent_semantic_task_id" in sql
    assert "DROP INDEX" not in sql
    assert "ENTITY_DISCOVERY" in sql
    assert "DOCUMENT_ENRICH" in sql
    for column in (
        "task_type",
        "batch_id",
        "file_path_snapshot",
        "progress",
        "input_fingerprint",
        "input_checksum",
        "method_version",
        "index_version",
        "request_params",
        "extra_params",
        "result_payload",
        "error_code",
        "failure_kind",
        "outcome_uncertain",
        "worker_id",
        "lease_expires_at",
        "heartbeat_at",
    ):
        assert column in sql
    assert "definition_version" not in sql
    assert "chk_knowledge_semantic_task_type" in sql
    assert "chk_knowledge_semantic_task_status" in sql
    assert "chk_knowledge_semantic_task_progress" in sql
    assert "chk_knowledge_semantic_batch_counts" in sql
    assert "current_stage varchar(32)" in sql
    assert "uq_knowledge_semantic_task_active_per_file" in sql
    assert "WHERE status IN ('pending', 'running')" in sql
    assert "idx_knowledge_semantic_task_kb_type_latest" in sql
    assert "idx_knowledge_semantic_task_kb_file_type_latest" in sql
    assert "idx_knowledge_semantic_task_batch" in sql
    assert "idx_knowledge_semantic_task_idempotency" in sql
