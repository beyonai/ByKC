"""Contract tests for additive File Build SQL migrations."""

from pathlib import Path

SQL_DIR = Path(__file__).resolve().parents[3] / "src/by_qa/knowledge_base/sql"


def _sql(name: str) -> str:
    return " ".join((SQL_DIR / name).read_text(encoding="utf-8").split())


def test_file_build_schema_is_added_only_by_incremental_migrations():
    batch = _sql("038_knowledge_build_batch.sql")
    extension = _sql("039_knowledge_build_task_background_extension.sql")
    backfill = _sql("040_knowledge_build_task_legacy_backfill.sql.tpl")
    constraints = _sql("041_knowledge_build_task_active_constraints.sql")

    assert "CREATE TABLE knowledge_build_batch" in batch
    assert "acceptance_skipped_count" in batch
    assert "extra_params jsonb NOT NULL DEFAULT '{}'::jsonb" in batch
    assert "ALTER TABLE knowledge_build_task" in extension
    assert "ADD COLUMN build_profile jsonb" in extension
    assert "ADD COLUMN extra_params jsonb NOT NULL DEFAULT '{}'::jsonb" in extension
    assert "{{ embedding_table_name }}" in backfill
    assert '"legacy":true' in backfill
    assert "MIGRATION_INTERRUPTED" in backfill
    assert (
        "DROP INDEX IF EXISTS uq_knowledge_build_task_running_per_file" in constraints
    )
    assert "CREATE UNIQUE INDEX uq_knowledge_build_task_active_per_file" in constraints
    assert "WHERE status IN ('pending', 'running')" in constraints
    assert "DROP TABLE" not in " ".join((batch, extension, backfill, constraints))


def test_historical_build_schema_files_remain_baseline_only():
    schema = _sql("006_knowledge_build_task.sql")
    indexes = _sql("013_knowledge_build_task_indexes.sql")

    assert "build_profile" not in schema
    assert "knowledge_build_batch" not in schema
    assert "uq_knowledge_build_task_running_per_file" in indexes
    assert "uq_knowledge_build_task_active_per_file" not in indexes
