"""Tests for KB schema bootstrap behavior."""

from pathlib import Path

from by_qa.knowledge_base import __file__ as knowledge_base_init_file
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
    normalize_embedding_table_name,
    split_sql_statements,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


def test_normalize_embedding_table_name_rewrites_unsafe_characters():
    """Embedding table names should be stable and SQL-safe."""
    assert (
        normalize_embedding_table_name("BGE-M3 Large") == "chunk_embedding_bge_m3_large"
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

    class FakeCursor:
        def __init__(self):
            self.executed: list[tuple[str, dict | None]] = []
            self._fetchone_results = [
                {"current_schema": "byai"},
                None,
            ]
            self._fetchall_results = [
                [{"nspname": "gaussdb"}, {"nspname": "public"}],
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
            self.commit_called = False

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            self.commit_called = True

    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
        sql_directory=tmp_path,
    )
    connection = FakeConnection()

    await service.apply(connection)

    set_config_call = connection.cursor_instance.executed[2]
    assert "set_config('search_path'" in set_config_call[0]
    assert set_config_call[1] == {"search_path": "byai,gaussdb,public"}
    assert (
        connection.cursor_instance.executed[-1][0] == "CREATE TABLE demo (path ltree);"
    )
    assert connection.commit_called


async def test_apply_rejects_existing_embedding_table_with_mismatched_dimension():
    """Bootstrap should fail fast when an existing embedding table uses another vector size."""

    class FakeCursor:
        def __init__(self):
            self.executed: list[str] = []
            self._results = [
                ("byai",),
                ("vector(3)",),
                (1,),
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement, params=None):
            self.executed.append(statement)

        async def fetchone(self):
            return self._results.pop(0)

        async def fetchall(self):
            return []

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.commit_called = False

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            self.commit_called = True

    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )
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

    assert not connection.commit_called


async def test_apply_rejects_existing_embedding_table_with_dict_rows():
    """Bootstrap should also handle psycopg dict_row results from the real runtime."""

    class FakeCursor:
        def __init__(self):
            self._results = [
                {"current_schema": "byai"},
                {"format_type": "vector(3)"},
                {"count": 1},
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement, params=None):
            return None

        async def fetchone(self):
            return self._results.pop(0)

        async def fetchall(self):
            return []

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()

        def cursor(self):
            return self.cursor_instance

        async def commit(self):
            raise AssertionError("commit should not be called")

    service = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bge-m3",
        embedding_dimension=1024,
    )

    try:
        await service.apply(FakeConnection())
    except KnowledgeBaseConfigurationError as exc:
        assert "vector(3)" in str(exc)
    else:
        raise AssertionError("expected KnowledgeBaseConfigurationError")
