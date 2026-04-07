"""Checkpointer factory for creating persistence backends."""

# pylint: disable=ungrouped-imports

import sqlite3

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

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


def _create_sync_opengauss_saver(
    settings: Settings,
    dsn: str | None,
) -> BaseCheckpointSaver:
    """Create a sync openGauss saver using LangGraph's PostgreSQL saver."""
    if PostgresSaver is None or OpenGaussSaver is None:
        raise ImportError(
            "openGauss backend requires langgraph-checkpoint-postgres to be installed"
        )

    opengauss_dsn = dsn or settings.checkpointer_opengauss_dsn
    if not opengauss_dsn:
        raise ValueError("CHECKPOINTER_OPENGAUSS_DSN is required for opengauss backend")

    from psycopg import Connection
    from psycopg.rows import dict_row

    conn = Connection.connect(
        opengauss_dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
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

    opengauss_dsn = dsn or settings.checkpointer_opengauss_dsn
    if not opengauss_dsn:
        raise ValueError("CHECKPOINTER_OPENGAUSS_DSN is required for opengauss backend")

    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    conn = await AsyncConnection.connect(
        opengauss_dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    return AsyncOpenGaussSaver(conn)


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
        path = sqlite_path or settings.checkpointer_sqlite_path
        conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver

    if backend == "memory":
        return InMemorySaver()

    if backend == "opengauss":
        saver = _create_sync_opengauss_saver(
            settings, opengauss_dsn or settings.checkpointer_opengauss_dsn
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

        path = sqlite_path or settings.checkpointer_sqlite_path
        conn = await aiosqlite.connect(path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        return saver

    if backend == "memory":
        return InMemorySaver()

    if backend == "opengauss":
        saver = await _create_async_opengauss_saver(
            settings, opengauss_dsn or settings.checkpointer_opengauss_dsn
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
