"""Unit tests for the shared per-file processing task repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from by_qa.knowledge_base.repositories.knowledge_build_task_repository import (
    KnowledgeBuildTaskRepository,
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


async def test_legacy_build_methods_are_explicitly_scoped_to_file_build():
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 1}, {"kid": 2}])

    await repo.get_latest_by_fs_entry_id(cursor, fs_entry_id=11)
    await repo.create_task(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        status="running",
        current_step="parse",
    )
    await repo.update_task(
        cursor,
        task_id=2,
        status="succeeded",
        current_step="finished",
        finished=True,
    )
    await repo.delete_for_fs_entry_id(cursor, fs_entry_id=11)

    for sql, params in cursor.executed:
        assert "task_type" in sql
        assert params["task_type"] == "FILE_BUILD"
    assert "task_type = %(task_type)s" in cursor.executed[0][0]
    assert "task_type" in cursor.executed[1][0].split("VALUES", maxsplit=1)[0]
    assert "task_type = %(task_type)s" in cursor.executed[2][0]
    assert "task_type = %(task_type)s" in cursor.executed[3][0]


async def test_create_processing_task_persists_versions_and_json_request():
    row = {"kid": 91, "task_type": "ENTITY_DISCOVERY"}
    cursor = FakeCursor(fetchone_results=[row])
    repo = KnowledgeBuildTaskRepository()

    result = await repo.create_processing_task(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        task_type="entity_discovery",
        status="PENDING",
        batch_id="ed-1",
        current_step="queued",
        progress=0,
        input_fingerprint="fp-1",
        input_checksum="sha-1",
        definition_version="ke/1.0",
        method_version="discovery/1.0",
        index_version="ac/18",
        request_params={"maxEntities": 12, "text": "中文"},
    )

    assert result == row
    sql, params = cursor.executed[0]
    assert "INSERT INTO knowledge_build_task" in sql
    assert "%(request_params)s::jsonb" in sql
    assert params["task_type"] == "ENTITY_DISCOVERY"
    assert params["status"] == "pending"
    assert params["batch_id"] == "ed-1"
    assert params["input_checksum"] == "sha-1"
    assert json.loads(params["request_params"]) == {
        "maxEntities": 12,
        "text": "中文",
    }


async def test_update_processing_task_is_scoped_to_the_entity_task_type():
    row = {"kid": 91, "status": "succeeded"}
    cursor = FakeCursor(fetchone_results=[row])
    repo = KnowledgeBuildTaskRepository()

    result = await repo.update_processing_task(
        cursor,
        task_id=91,
        task_type="DOCUMENT_ENRICH",
        status="SUCCEEDED",
        current_step="persist",
        progress=100,
        result_payload={"warningCount": 2},
        started=True,
        finished=True,
    )

    assert result == row
    sql, params = cursor.executed[0]
    assert "task_type = %(task_type)s" in sql
    assert params["task_type"] == "DOCUMENT_ENRICH"
    assert params["status"] == "succeeded"
    assert params["progress"] == 100
    assert json.loads(params["result_payload"]) == {"warningCount": 2}


async def test_list_processing_tasks_filters_and_groups_latest_before_status():
    rows = [{"kid": 91}, {"kid": 90}]
    cursor = FakeCursor(fetchall_results=[rows])
    repo = KnowledgeBuildTaskRepository()

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


async def test_list_processing_tasks_without_type_excludes_file_build():
    cursor = FakeCursor()
    repo = KnowledgeBuildTaskRepository()

    await repo.list_processing_tasks(cursor, knowledge_base_id=7)

    sql, params = cursor.executed[0]
    assert "task.task_type IN ('ENTITY_DISCOVERY', 'DOCUMENT_ENRICH')" in sql
    assert "task_type" not in params


async def test_count_processing_tasks_uses_the_same_latest_grouping_and_filters():
    cursor = FakeCursor(fetchone_results=[{"total": 3}])
    repo = KnowledgeBuildTaskRepository()

    result = await repo.count_processing_tasks(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=11,
        statuses=["FAILED"],
        latest_only=False,
    )

    assert result == 3
    sql, params = cursor.executed[0]
    assert "PARTITION BY task.fs_entry_id, task.task_type" in sql
    assert "task.fs_entry_id = %(fs_entry_id)s" in sql
    assert params["statuses"] == ["failed"]
    assert params["latest_only"] is False


@pytest.mark.parametrize("task_type", ["FILE_BUILD", "UNKNOWN"])
async def test_processing_task_methods_reject_non_entity_types(task_type):
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor()

    with pytest.raises(ValueError, match="unsupported entity task type"):
        await repo.create_processing_task(
            cursor,
            knowledge_base_id=7,
            fs_entry_id=11,
            task_type=task_type,
        )


@pytest.mark.parametrize("progress", [-1, 101])
async def test_processing_task_methods_reject_invalid_progress(progress):
    repo = KnowledgeBuildTaskRepository()
    cursor = FakeCursor()

    with pytest.raises(ValueError, match="progress must be between 0 and 100"):
        await repo.create_processing_task(
            cursor,
            knowledge_base_id=7,
            fs_entry_id=11,
            task_type="ENTITY_DISCOVERY",
            progress=progress,
        )


def test_processing_task_migration_adds_columns_and_rebuilds_running_index():
    sql_dir = Path(__file__).resolve().parents[3] / "src/by_qa/knowledge_base/sql"
    migration = (
        sql_dir / "029_knowledge_build_task_processing_extension.sql"
    ).read_text()
    schema = (sql_dir / "006_knowledge_build_task.sql").read_text()
    indexes = (sql_dir / "013_knowledge_build_task_indexes.sql").read_text()

    for column in (
        "task_type",
        "batch_id",
        "progress",
        "input_fingerprint",
        "input_checksum",
        "definition_version",
        "enrich_version",
        "method_version",
        "index_version",
        "request_params",
        "result_payload",
        "error_code",
    ):
        assert column in migration
        assert column in schema
    assert "DROP INDEX IF EXISTS uq_knowledge_build_task_running_per_file" in migration
    assert "(fs_entry_id, task_type)" in migration
    assert "(fs_entry_id, task_type)" in indexes
