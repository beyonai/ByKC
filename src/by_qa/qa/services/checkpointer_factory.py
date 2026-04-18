"""Checkpointer factory for creating persistence backends."""

# pylint: disable=ungrouped-imports

import sqlite3

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from psycopg import sql

from by_qa.config import Settings, get_settings

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
except ImportError:
    SqliteSaver = None
    AsyncSqliteSaver = None

try:
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:
    PostgresSaver = None
    AsyncPostgresSaver = None

try:
    from by_qa.qa.services.opengauss_checkpointer import (
        AsyncOpenGaussSaver,
        OpenGaussSaver,
    )
except ImportError:
    OpenGaussSaver = None
    AsyncOpenGaussSaver = None


def _resolve_opengauss_dsn(settings: Settings, dsn: str | None) -> str:
    """Resolve explicit or shared DB_* openGauss DSN settings."""
    if dsn:
        return dsn
    build_opengauss_dsn = getattr(settings, "build_opengauss_dsn", None)
    if callable(build_opengauss_dsn):
        return build_opengauss_dsn()
    return getattr(settings, "resolved_checkpointer_opengauss_dsn", "")


def _create_sync_opengauss_saver(
    settings: Settings,
    dsn: str | None,
) -> BaseCheckpointSaver:
    """Create a sync openGauss saver using LangGraph's PostgreSQL saver."""
    if PostgresSaver is None or OpenGaussSaver is None:
        raise ImportError(
            "openGauss backend requires langgraph-checkpoint-postgres to be installed"
        )

    opengauss_dsn = _resolve_opengauss_dsn(settings, dsn)
    if not opengauss_dsn:
        raise ValueError(
            "DB_HOST, DB_USER, and DB_PASS are required for opengauss backend"
        )

    from psycopg import Connection
    from psycopg.rows import dict_row

    conn = Connection.connect(
        opengauss_dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    _ensure_schema(conn, getattr(settings, "db_schema", ""))
    return OpenGaussSaver(conn)


async def _create_async_opengauss_saver(
    settings: Settings,
    dsn: str | None,
) -> BaseCheckpointSaver:
    """Create an async openGauss saver using LangGraph's PostgreSQL saver."""
    if AsyncPostgresSaver is None or AsyncOpenGaussSaver is None:
        raise ImportError(
            "openGauss backend requires langgraph-checkpoint-postgres to be installed"
        )

    opengauss_dsn = _resolve_opengauss_dsn(settings, dsn)
    if not opengauss_dsn:
        raise ValueError(
            "DB_HOST, DB_USER, and DB_PASS are required for opengauss backend"
        )

    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    conn = await AsyncConnection.connect(
        opengauss_dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    await _ensure_schema_async(conn, getattr(settings, "db_schema", ""))
    return AsyncOpenGaussSaver(conn)


def _ensure_schema(connection, schema: str) -> None:
    """Create the configured schema when DB_SCHEMA is set."""
    schema = schema.strip()
    if not schema:
        return
    connection.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
    )


async def _ensure_schema_async(connection, schema: str) -> None:
    """Create the configured schema when DB_SCHEMA is set."""
    schema = schema.strip()
    if not schema:
        return
    await connection.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
    )


def create_checkpointer(
    settings: Settings | None = None,
    backend: str | None = None,
    sqlite_path: str | None = None,
    opengauss_dsn: str | None = None,
) -> BaseCheckpointSaver:
    """Create a synchronous checkpointer instance based on configuration."""
    if settings is None:
        settings = get_settings()

    backend = (backend or settings.checkpointer_backend).lower()

    if backend == "sqlite":
        if SqliteSaver is None:
            raise ImportError(
                "sqlite backend requires langgraph-checkpoint-sqlite to be installed"
            )
        path = sqlite_path or getattr(
            settings, "checkpointer_sqlite_path", "./data/checkpoints.db"
        )
        conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver

    if backend == "memory":
        return InMemorySaver()

    if backend == "opengauss":
        saver = _create_sync_opengauss_saver(
            settings, _resolve_opengauss_dsn(settings, opengauss_dsn)
        )
        saver.setup()
        return saver

    raise ValueError(
        "Unknown checkpointer backend: "
        f"{backend}. Supported backends: sqlite, memory, opengauss"
    )


async def create_checkpointer_async(
    settings: Settings | None = None,
    backend: str | None = None,
    sqlite_path: str | None = None,
    opengauss_dsn: str | None = None,
) -> BaseCheckpointSaver:
    """Create an asynchronous checkpointer instance based on configuration."""
    if settings is None:
        settings = get_settings()

    backend = (backend or settings.checkpointer_backend).lower()

    if backend == "sqlite":
        if AsyncSqliteSaver is None:
            raise ImportError(
                "sqlite backend requires langgraph-checkpoint-sqlite to be installed"
            )
        import aiosqlite

        path = sqlite_path or getattr(
            settings, "checkpointer_sqlite_path", "./data/checkpoints.db"
        )
        conn = await aiosqlite.connect(path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        return saver

    if backend == "memory":
        return InMemorySaver()

    if backend == "opengauss":
        saver = await _create_async_opengauss_saver(
            settings, _resolve_opengauss_dsn(settings, opengauss_dsn)
        )
        await saver.setup()
        return saver

    raise ValueError(
        "Unknown checkpointer backend: "
        f"{backend}. Supported backends: sqlite, memory, opengauss"
    )


def get_checkpointer_backend_name(checkpointer: BaseCheckpointSaver) -> str:
    """Get the backend name from a checkpointer instance."""
    if SqliteSaver is not None and isinstance(
        checkpointer, (SqliteSaver, AsyncSqliteSaver)
    ):
        return "sqlite"
    if OpenGaussSaver is not None and isinstance(checkpointer, OpenGaussSaver):
        return "opengauss"
    if AsyncOpenGaussSaver is not None and isinstance(
        checkpointer, AsyncOpenGaussSaver
    ):
        return "opengauss"
    if isinstance(checkpointer, InMemorySaver):
        return "memory"
    return "unknown"
