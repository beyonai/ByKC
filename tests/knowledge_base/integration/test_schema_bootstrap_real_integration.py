"""Real OpenGauss integration coverage for concurrent schema migrations.

This module intentionally uses independent production connection and bootstrap
service instances.  It must not use mocks, fakes, monkeypatching, or dependency
overrides: the concurrency guarantee belongs to OpenGauss session locks and the
migration ledger, not to an in-process coordinator.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql

from by_qa.config import get_settings
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)


def _bootstrap_service(sql_directory: Path) -> KnowledgeBaseSchemaBootstrapService:
    return KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="bootstrap-concurrency-probe",
        embedding_dimension=3,
        sql_directory=sql_directory,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_bootstrap_applies_each_migration_once_and_releases_lock(
    tmp_path: Path,
) -> None:
    """Two application instances must serialize DDL and persist one ledger row."""

    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail(
            "Real bootstrap integration requires DB_HOST, DB_USER, and DB_PASS",
            pytrace=False,
        )

    schema_name = f"kb_bootstrap_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(update={"db_schema": schema_name})
    connection_factory = build_connection_factory(settings)
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()

    # This is the ledger bootstrap migration used by an empty database.  The
    # second migration is deliberately non-idempotent: executing it twice would
    # fail on the primary key, so success cannot be explained by IF NOT EXISTS.
    ledger_sql = """
        CREATE TABLE knowledge_schema_migration (
            version VARCHAR(255) PRIMARY KEY,
            checksum VARCHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    probe_sql = """
        CREATE TABLE concurrent_bootstrap_probe (
            probe_id INTEGER PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        SELECT pg_sleep(1);
        INSERT INTO concurrent_bootstrap_probe (probe_id) VALUES (1);
        """
    (migration_directory / "032_knowledge_schema_migration.sql").write_text(
        ledger_sql,
        encoding="utf-8",
    )
    (migration_directory / "033_concurrent_bootstrap_probe.sql").write_text(
        probe_sql,
        encoding="utf-8",
    )

    connections = []
    setup_connection = await connection_factory()
    await setup_connection.close()
    try:
        first_connection = await connection_factory()
        second_connection = await connection_factory()
        connections.extend((first_connection, second_connection))

        await asyncio.gather(
            _bootstrap_service(migration_directory).apply(first_connection),
            _bootstrap_service(migration_directory).apply(second_connection),
        )

        async with first_connection.cursor() as cursor:
            await cursor.execute(
                "SELECT probe_id FROM concurrent_bootstrap_probe ORDER BY probe_id"
            )
            assert await cursor.fetchall() == [{"probe_id": 1}]
            await cursor.execute(
                """
                SELECT version, checksum, applied_at
                FROM knowledge_schema_migration
                ORDER BY version
                """
            )
            ledger_rows = list(await cursor.fetchall())

        assert [row["version"] for row in ledger_rows] == [
            "032_knowledge_schema_migration.sql",
            "033_concurrent_bootstrap_probe.sql",
        ]
        assert [row["checksum"] for row in ledger_rows] == [
            hashlib.sha256(ledger_sql.strip().encode("utf-8")).hexdigest(),
            hashlib.sha256(probe_sql.strip().encode("utf-8")).hexdigest(),
        ]
        assert all(row["applied_at"] is not None for row in ledger_rows)

        # Keep both original sessions alive.  A fresh service instance must
        # still finish promptly, proving apply() explicitly released its
        # session-level advisory lock.  The non-idempotent insert also proves a
        # restart skips migrations already recorded in the ledger.
        restart_connection = await connection_factory()
        connections.append(restart_connection)
        await asyncio.wait_for(
            _bootstrap_service(migration_directory).apply(restart_connection),
            timeout=5,
        )

        async with restart_connection.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) AS total FROM concurrent_bootstrap_probe"
            )
            assert (await cursor.fetchone())["total"] == 1
            await cursor.execute(
                "SELECT COUNT(*) AS total FROM knowledge_schema_migration"
            )
            assert (await cursor.fetchone())["total"] == 2
    finally:
        for connection in reversed(connections):
            await connection.close()
        cleanup_connection = await connection_factory()
        try:
            await cleanup_connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup_connection.commit()
        finally:
            await cleanup_connection.close()
