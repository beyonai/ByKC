"""Database infrastructure helpers for knowledge base ingestion."""

import re
from typing import Awaitable, Callable

from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row

from by_qa.config import Settings


def build_connection_factory(
    settings: Settings,
) -> Callable[[], Awaitable[AsyncConnection]]:
    """Build an async psycopg connection factory for knowledge base persistence."""

    async def connect() -> AsyncConnection:
        connection = await AsyncConnection.connect(
            settings.resolved_kb_opengauss_dsn,
            autocommit=False,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        await _ensure_schema(connection, settings.db_schema)
        await _prepare_extension_search_path(connection, settings.db_schema)
        return connection

    return connect


async def _ensure_schema(connection: AsyncConnection, schema: str) -> None:
    """Create the configured schema when DB_SCHEMA is set."""
    schema = schema.strip()
    if not schema:
        return

    await connection.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
    )
    await connection.commit()


async def _prepare_extension_search_path(
    connection: AsyncConnection,
    schema: str,
) -> None:
    """Include extension schemas in each runtime connection's search path."""
    cursor = await connection.execute(
        """
        SELECT n.nspname
        FROM pg_extension e
        JOIN pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname IN ('ltree', 'pg_trgm')
        ORDER BY e.extname
        """
    )
    extension_schemas = [
        _get_scalar_value(row, "nspname") for row in await cursor.fetchall()
    ]
    schemas = _dedupe_schema_names([schema, *extension_schemas, "public"])
    if not schemas:
        return

    await connection.execute(
        "SELECT set_config('search_path', %(search_path)s, false)",
        {"search_path": ",".join(_format_search_path_schema(s) for s in schemas)},
    )


def _dedupe_schema_names(schemas: list[str | None]) -> list[str]:
    """Return schema names without blanks or duplicates, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for schema in schemas:
        if not schema:
            continue
        normalized = schema.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _format_search_path_schema(schema: str) -> str:
    """Quote schema names only when they are not safe unquoted identifiers."""
    if re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        return schema
    return '"' + schema.replace('"', '""') + '"'


def _get_scalar_value(row, key: str):
    """Read a single-column result from either tuple-like or mapping-like rows."""
    if isinstance(row, dict):
        return row[key]
    return row[0]
