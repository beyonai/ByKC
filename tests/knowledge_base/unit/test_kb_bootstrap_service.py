"""Tests for KB schema bootstrap behavior."""

import hashlib
from pathlib import Path

import pytest

from by_qa.knowledge_base import __file__ as knowledge_base_init_file
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
    SchemaMigration,
    normalize_embedding_table_name,
    normalize_entity_embedding_table_name,
    split_sql_statements,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


def test_normalize_embedding_table_name_rewrites_unsafe_characters():
    """Embedding table names should be stable and SQL-safe."""
    assert (
        normalize_embedding_table_name("BGE-M3 Large") == "chunk_embedding_bge_m3_large"
    )


def test_normalize_entity_embedding_table_name_rewrites_unsafe_characters():
    assert (
        normalize_entity_embedding_table_name("Text-Embedding-V4")
        == "knowledge_entity_embedding_text_embedding_v4"
    )


def test_build_schema_statements_include_current_chunk_and_projection_tables():
    """Bootstrap DDL should contain the current chunk, retrieval, cache, and embedding tables."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    ddl = "\n".join(service.build_schema_statements())

    assert "create table if not exists knowledge_chunk" in ddl.lower()
    assert "knowledge_chunk_retrieval_mv" in ddl
    assert "knowledge_fetch_cache_index" in ddl
    assert "chunk_embedding_bge_m3" in ddl
    assert "create table if not exists knowledge_entity" in ddl.lower()
    assert "knowledge_entity_embedding_bge_m3" in ddl
    assert "representation in ('full', 'local_name')" in ddl.lower()
    assert "vector(1024)" in ddl


def test_build_schema_statements_enable_ltree_and_pg_trgm_for_current_stack():
    """Bootstrap DDL should restore ltree and pg_trgm now that the custom image provides them."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    ddl = "\n".join(service.build_schema_statements())

    assert "CREATE EXTENSION IF NOT EXISTS ltree;" in ddl
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm;" in ddl
    assert "path_ltree ltree NOT NULL" in ddl
    assert "gin_trgm_ops" in ddl


def test_build_schema_statements_make_fs_entry_uniqueness_apply_only_to_active_rows():
    """Filesystem sibling-name uniqueness should apply only to non-deleted rows."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    ddl = "\n".join(service.build_schema_statements())

    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_sibling_name_active"
        in ddl
    )
    assert "ON knowledge_fs_entry (knowledge_base_id, parent_entry_id, name)" in ddl
    assert "WHERE is_deleted = false;" in ddl


def test_build_schema_statements_make_top_level_sibling_names_unique():
    """Incremental DDL should add uniqueness for top-level entries with NULL parent ids."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    ddl = "\n".join(service.build_schema_statements())

    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_top_level_sibling_name_active"
        in ddl
    )
    assert "ON knowledge_fs_entry (knowledge_base_id, name)" in ddl
    assert "WHERE parent_entry_id IS NULL" in ddl
    assert "AND is_root = false" in ddl
    assert "AND is_deleted = false;" in ddl


def test_build_schema_statements_make_metadata_values_self_contained():
    """Metadata value DDL should not depend on the removed property definition table."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    ddl = "\n".join(service.build_schema_statements())

    assert "property_name varchar(128) NOT NULL" in ddl
    assert "value_type varchar(32) NOT NULL" in ddl
    assert (
        "ON knowledge_file_metadata_value (fs_entry_id, property_name, value_type)"
        in ddl
    )
    assert "DROP TABLE IF EXISTS knowledge_metadata_property_def CASCADE" in ddl


def test_build_schema_statements_make_knowledge_base_names_unique():
    """Incremental DDL should add uniqueness for active knowledge base names."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    ddl = "\n".join(service.build_schema_statements())

    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_base_name_active" in ddl
    assert "ON knowledge_base (kb_name)" in ddl
    assert "WHERE is_deleted = false;" in ddl


def test_build_schema_statements_loads_external_sql_files(tmp_path: Path):
    """Bootstrap should load static SQL files and render the dynamic embedding template."""
    (tmp_path / "001_base.sql").write_text(
        "CREATE TABLE base_table (id bigint);", encoding="utf-8"
    )
    (tmp_path / "002_index.sql").write_text(
        "CREATE INDEX idx_base_table_id ON base_table (id);",
        encoding="utf-8",
    )
    (tmp_path / "010_embedding_table.sql.tpl").write_text(
        (
            "CREATE TABLE {{ embedding_table_name }} "
            "(embedding vector({{ embedding_dimension }}));"
        ),
        encoding="utf-8",
    )
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
        sql_directory=tmp_path,
    )

    statements = service.build_schema_statements()

    assert statements[0] == "CREATE TABLE base_table (id bigint);"
    assert statements[1] == "CREATE INDEX idx_base_table_id ON base_table (id);"
    assert (
        statements[2] == "CREATE TABLE chunk_embedding_bge_m3 (embedding vector(1024));"
    )


def test_default_sql_directory_points_to_packaged_resources():
    """Bootstrap should default to the SQL directory shipped inside the package."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    assert service.sql_directory == (
        Path(knowledge_base_init_file).resolve().parent / "sql"
    )


def test_default_sql_directory_contains_embedding_template():
    """Packaged SQL resources should include the dynamic embedding template."""
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    assert (service.sql_directory / "014_embedding_table.sql.tpl").is_file()


def test_split_sql_statements_handles_multiple_top_level_statements():
    """Bootstrap should split multi-statement files without breaking DO blocks."""
    script = """
    CREATE EXTENSION IF NOT EXISTS vector;
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1) THEN
            CREATE TABLE demo (kid bigint);
        END IF;
    END $$;
    CREATE INDEX idx_demo_kid ON demo (kid);
    """

    statements = split_sql_statements(script)

    assert statements == [
        "CREATE EXTENSION IF NOT EXISTS vector;",
        (
            "DO $$\n"
            "    BEGIN\n"
            "        IF NOT EXISTS (SELECT 1) THEN\n"
            "            CREATE TABLE demo (kid bigint);\n"
            "        END IF;\n"
            "    END $$;"
        ),
        "CREATE INDEX idx_demo_kid ON demo (kid);",
    ]


async def test_apply_adds_existing_extension_schemas_to_search_path(tmp_path: Path):
    """Bootstrap should resolve extension types even when they live outside the app schema."""

    (tmp_path / "001_demo.sql").write_text(
        "CREATE TABLE demo (path ltree);",
        encoding="utf-8",
    )
    (tmp_path / "032_knowledge_schema_migration.sql").write_text(
        "CREATE TABLE knowledge_schema_migration "
        "(version varchar(255) PRIMARY KEY, checksum varchar(64) NOT NULL);",
        encoding="utf-8",
    )
    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
        sql_directory=tmp_path,
    )
    ledger_migration = next(
        migration
        for migration in service._load_migrations()
        if migration.version == "032_knowledge_schema_migration.sql"
    )

    class FakeCursor:
        def __init__(self):
            self.executed: list[tuple[str, dict | None]] = []
            self._fetchone_results = [
                {"current_schema": "byai"},
                {"database_name": "byqa", "schema_name": "byai"},
                {"ledger_exists": False},
                {"count": 0},
                {"legacy_complete": False},
                None,
            ]
            self._fetchall_results = [
                [{"nspname": "gaussdb"}, {"nspname": "public"}],
                [(ledger_migration.version, ledger_migration.checksum)],
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement, params=None):
            self.executed.append((statement, params))

        async def fetchone(self):
            return self._fetchone_results.pop(0)

        async def fetchall(self):
            return self._fetchall_results.pop(0)

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.commit_count = 0
            self.rollback_count = 0

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            self.commit_count += 1

        async def rollback(self):
            self.rollback_count += 1

    connection = FakeConnection()

    await service.apply(connection)

    set_config_call = connection.cursor_instance.executed[2]
    assert "set_config('search_path'" in set_config_call[0]
    assert set_config_call[1] == {"search_path": "byai,gaussdb,public"}
    assert any(
        statement == "CREATE TABLE demo (path ltree);"
        for statement, _ in connection.cursor_instance.executed
    )
    assert connection.commit_count >= 4
    assert any(
        "pg_advisory_unlock" in statement
        for statement, _ in connection.cursor_instance.executed
    )
    assert (
        sum(
            "CREATE TABLE knowledge_schema_migration" in statement
            for statement, _ in connection.cursor_instance.executed
        )
        == 1
    )


async def test_apply_rejects_existing_embedding_table_with_mismatched_dimension():
    """Bootstrap should fail fast when an existing embedding table uses another vector size."""

    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )
    applied_rows = [
        (migration.version, migration.checksum)
        for migration in service._load_migrations()
    ]

    class FakeCursor:
        def __init__(self):
            self.executed: list[str] = []
            self._results = [
                ("byai",),
                ("byqa", "byai"),
                (True,),
                (applied_rows[-1][1],),
                (len(applied_rows),),
                ("vector(3)",),
                (1,),
            ]
            self._fetchall_results = [[], applied_rows]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement, params=None):
            self.executed.append(statement)

        async def fetchone(self):
            return self._results.pop(0)

        async def fetchall(self):
            return self._fetchall_results.pop(0)

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.commit_count = 0
            self.rollback_count = 0

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            self.commit_count += 1

        async def rollback(self):
            self.rollback_count += 1

    connection = FakeConnection()

    try:
        await service.apply(connection)
    except KnowledgeBaseConfigurationError as exc:
        message = str(exc)
        assert "chunk_embedding_bge_m3" in message
        assert "vector(3)" in message
        assert "EMBEDDING_DIMENSION=1024" in message
    else:
        raise AssertionError("expected KnowledgeBaseConfigurationError")

    assert connection.rollback_count >= 1
    assert any(
        "pg_advisory_unlock" in statement
        for statement in connection.cursor_instance.executed
    )


def test_dynamic_embedding_migration_identity_and_checksum_are_config_scoped(
    tmp_path: Path,
):
    """A model gets one stable migration while dimension drift changes its checksum."""
    (tmp_path / "014_embedding_table.sql.tpl").write_text(
        "CREATE TABLE {{ embedding_table_name }} (embedding vector({{ embedding_dimension }}));",
        encoding="utf-8",
    )
    (tmp_path / "032_knowledge_schema_migration.sql").write_text(
        "CREATE TABLE knowledge_schema_migration (version text);",
        encoding="utf-8",
    )

    first = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
        sql_directory=tmp_path,
    )._load_migrations()[0]
    same = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
        sql_directory=tmp_path,
    )._load_migrations()[0]
    changed_dimension = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=768,
        sql_directory=tmp_path,
    )._load_migrations()[0]
    changed_model = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="text-embedding-3",
        embedding_dimension=1024,
        sql_directory=tmp_path,
    )._load_migrations()[0]

    assert first.version == "014_embedding_table.sql.tpl:chunk_embedding_bge_m3"
    assert same == first
    assert changed_dimension.version == first.version
    assert changed_dimension.checksum != first.checksum
    assert changed_model.version != first.version


def test_applied_migration_checksum_drift_fails_fast():
    migration = SchemaMigration(
        version="033_demo.sql",
        checksum=hashlib.sha256(b"new").hexdigest(),
        statements=("SELECT 1;",),
        numeric_version=33,
    )

    with pytest.raises(KnowledgeBaseConfigurationError, match="checksum drift"):
        KnowledgeBaseSchemaBootstrapService._validate_migration_checksums(
            [migration],
            {migration.version: hashlib.sha256(b"old").hexdigest()},
        )


async def test_migration_deadlock_is_rolled_back_and_retried():
    class DeadlockError(Exception):
        sqlstate = "40P01"

    class FakeCursor:
        def __init__(self):
            self.probe_attempts = 0
            self.records = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement, params=None):
            if statement == "CREATE TABLE probe (kid bigint);":
                self.probe_attempts += 1
                if self.probe_attempts == 1:
                    raise DeadlockError()
            if "INSERT INTO knowledge_schema_migration" in statement:
                self.records += 1

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
        deadlock_retry_base_seconds=0.25,
        sleep=fake_sleep,
        jitter=lambda _lower, upper: upper,
    )
    migration = SchemaMigration(
        version="033_probe.sql",
        checksum="a" * 64,
        statements=("CREATE TABLE probe (kid bigint);",),
        numeric_version=33,
    )
    connection = FakeConnection()

    await service._apply_migration_with_retry(connection, migration)

    assert connection.cursor_instance.probe_attempts == 2
    assert connection.cursor_instance.records == 1
    assert connection.rollbacks == 1
    assert connection.commits == 1
    assert delays == [0.25]


async def test_legacy_baseline_records_only_through_030_and_existing_template():
    migrations = [
        SchemaMigration("000_extensions.sql", "0" * 64, ("SELECT 0;",), 0),
        SchemaMigration(
            "014_embedding_table.sql.tpl:chunk_embedding_bge_m3",
            "1" * 64,
            ("SELECT 14;",),
            14,
        ),
        SchemaMigration("030_relations.sql", "2" * 64, ("SELECT 30;",), 30),
        SchemaMigration("031_backfill.sql", "3" * 64, ("SELECT 31;",), 31),
        SchemaMigration(
            "032_knowledge_schema_migration.sql",
            "4" * 64,
            ("SELECT 32;",),
            32,
        ),
    ]

    class FakeCursor:
        def __init__(self):
            self.results = [
                {"count": 0},
                {"legacy_complete": True, "embedding_table_exists": True},
            ]
            self.recorded: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement, params=None):
            if "INSERT INTO knowledge_schema_migration" in statement:
                self.recorded.append(params["version"])

        async def fetchone(self):
            return self.results.pop(0)

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.commits = 0

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            self.commits += 1

    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )
    connection = FakeConnection()

    await service._baseline_legacy_schema(connection, migrations)

    assert connection.cursor_instance.recorded == [
        "000_extensions.sql",
        "014_embedding_table.sql.tpl:chunk_embedding_bge_m3",
        "030_relations.sql",
    ]
    assert connection.commits == 1


async def test_apply_rejects_existing_embedding_table_with_dict_rows():
    """Bootstrap should also handle psycopg dict_row results from the real runtime."""

    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )
    applied_rows = [
        {"version": migration.version, "checksum": migration.checksum}
        for migration in service._load_migrations()
    ]

    class FakeCursor:
        def __init__(self):
            self._results = [
                {"current_schema": "byai"},
                {"database_name": "byqa", "schema_name": "byai"},
                {"ledger_exists": True},
                {"checksum": applied_rows[-1]["checksum"]},
                {"count": len(applied_rows)},
                {"format_type": "vector(3)"},
                {"count": 1},
            ]
            self._fetchall_results = [[], applied_rows]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement, params=None):
            return None

        async def fetchone(self):
            return self._results.pop(0)

        async def fetchall(self):
            return self._fetchall_results.pop(0)

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            return None

        async def rollback(self):
            return None

    try:
        await service.apply(FakeConnection())
    except KnowledgeBaseConfigurationError as exc:
        assert "vector(3)" in str(exc)
    else:
        raise AssertionError("expected KnowledgeBaseConfigurationError")
