"""Integration tests for OpenGaussSaver schema migration.

Regression coverage for the bug where ``_task_path_exists_query`` did not
filter by ``table_schema`` and therefore reported "task_path already exists"
for any newly created schema once a single schema in the database had been
migrated. Result: every schema after the first one ended up with a
``checkpoint_writes`` table missing the ``task_path`` column, while
``checkpoint_migrations`` still recorded v=last as applied.
"""

# pylint: disable=redefined-outer-name,wrong-import-position

from __future__ import annotations

import os
import uuid
from urllib.parse import quote

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("langgraph.checkpoint.postgres")

from psycopg import sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from by_qa.qa.services.opengauss_checkpointer import (  # noqa: E402
    AsyncOpenGaussSaver,
    OpenGaussSaver,
)

pytestmark = pytest.mark.integration


DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "15432"
DEFAULT_DB_DATABASE = "postgres"
DEFAULT_DB_USER = "gaussdb"
DEFAULT_DB_PASS = "OpenGauss#2026"


def _base_dsn() -> str:
    host = os.getenv("DB_HOST", DEFAULT_DB_HOST)
    port = os.getenv("DB_PORT", DEFAULT_DB_PORT)
    database = os.getenv("DB_DATABASE", DEFAULT_DB_DATABASE)
    user = os.getenv("DB_USER", DEFAULT_DB_USER)
    password = quote(os.getenv("DB_PASS", DEFAULT_DB_PASS), safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _set_search_path(conn, schema: str) -> None:
    conn.execute(
        sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
    )


async def _set_search_path_async(conn, schema: str) -> None:
    await conn.execute(
        sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
    )


def _new_schema_name() -> str:
    return f"by_qa_ckpt_{uuid.uuid4().hex[:10]}"


def _create_schema(schema: str) -> None:
    with psycopg.connect(_base_dsn(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
        )


def _drop_schema(schema: str) -> None:
    with psycopg.connect(_base_dsn(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )


def _column_exists(schema: str, table: str, column: str) -> bool:
    with psycopg.connect(_base_dsn(), autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT 1 AS present
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (schema, table, column),
        ).fetchone()
    return row is not None


@pytest.fixture
def two_fresh_schemas():
    schema_a = _new_schema_name()
    schema_b = _new_schema_name()
    _create_schema(schema_a)
    _create_schema(schema_b)
    try:
        yield schema_a, schema_b
    finally:
        _drop_schema(schema_a)
        _drop_schema(schema_b)


def test_sync_setup_in_two_schemas_each_has_task_path(two_fresh_schemas):
    """Setting up the saver in schema B after schema A must still add task_path to B."""
    schema_a, schema_b = two_fresh_schemas

    for schema in (schema_a, schema_b):
        with psycopg.connect(
            _base_dsn(),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as conn:
            _set_search_path(conn, schema)
            saver = OpenGaussSaver(conn)
            saver.setup()

    assert _column_exists(schema_a, "checkpoint_writes", "task_path"), (
        f"schema {schema_a} missing checkpoint_writes.task_path after setup()"
    )
    assert _column_exists(schema_b, "checkpoint_writes", "task_path"), (
        f"schema {schema_b} missing checkpoint_writes.task_path after setup() — "
        "schema-leakage regression in _task_path_exists_query"
    )


async def test_async_setup_in_two_schemas_each_has_task_path(two_fresh_schemas):
    schema_a, schema_b = two_fresh_schemas

    for schema in (schema_a, schema_b):
        conn = await psycopg.AsyncConnection.connect(
            _base_dsn(),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        try:
            await _set_search_path_async(conn, schema)
            saver = AsyncOpenGaussSaver(conn)
            await saver.setup()
        finally:
            await conn.close()

    assert _column_exists(schema_a, "checkpoint_writes", "task_path")
    assert _column_exists(schema_b, "checkpoint_writes", "task_path")
