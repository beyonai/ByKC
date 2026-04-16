"""Database infrastructure helpers for knowledge base ingestion."""

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
