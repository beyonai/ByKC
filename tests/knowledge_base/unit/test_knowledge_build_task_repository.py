"""Regression tests keeping the historical build-task contract unchanged."""

from __future__ import annotations

from pathlib import Path

from by_qa.knowledge_base.repositories.knowledge_build_task_repository import (
    KnowledgeBuildTaskRepository,
)

SQL_DIR = Path(__file__).resolve().parents[3] / "src/by_qa/knowledge_base/sql"


class FakeCursor:
    def __init__(self, *, fetchone_results=None):
        self.executed: list[tuple[str, dict | None]] = []
        self._fetchone_results = list(fetchone_results or [])

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchone(self):
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None


async def test_build_task_repository_keeps_the_file_build_schema_contract():
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
