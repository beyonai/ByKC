"""Real upgrade coverage for the additive File Build migrations."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.errors import CheckViolation

from by_qa.config import get_settings
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.services import bootstrap_service as bootstrap_module
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_MODEL_NAME = "file-build-legacy-upgrade"
_EMBEDDING_DIMENSION = 3


def _bootstrap(
    sql_directory: Path | None = None,
) -> KnowledgeBaseSchemaBootstrapService:
    return KnowledgeBaseSchemaBootstrapService(
        embedding_model_name=_MODEL_NAME,
        embedding_dimension=_EMBEDDING_DIMENSION,
        sql_directory=sql_directory,
    )


def _copy_migrations_through_037(target: Path) -> None:
    source = Path(bootstrap_module.__file__).resolve().parents[1] / "sql"
    target.mkdir()
    for path in source.iterdir():
        prefix = path.name.split("_", maxsplit=1)[0]
        if path.is_file() and prefix.isdigit() and int(prefix) <= 37:
            shutil.copy2(path, target / path.name)


async def test_legacy_file_build_schema_upgrades_through_041_once(
    tmp_path: Path,
) -> None:
    """A populated schema at 037 must upgrade without losing Build history."""

    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)

    schema_name = f"file_build_upgrade_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(update={"db_schema": schema_name})
    connection_factory = build_connection_factory(settings)
    legacy_sql_directory = tmp_path / "migrations-through-037"
    _copy_migrations_through_037(legacy_sql_directory)

    bootstrap_connection = await connection_factory()
    try:
        await _bootstrap(legacy_sql_directory).apply(bootstrap_connection)
    finally:
        await bootstrap_connection.close()

    setup_connection = await connection_factory()
    try:
        cursor = setup_connection.cursor()
        await cursor.execute(
            "INSERT INTO knowledge_base (kb_name) VALUES (%(name)s) RETURNING kid",
            {"name": f"legacy-build-upgrade-{uuid4().hex}"},
        )
        knowledge_base_id = int((await cursor.fetchone())["kid"])
        file_ids: dict[str, int] = {}
        for status in ("complete", "running", "failed", "unsupported"):
            await cursor.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, entry_type, is_root, name, path_ltree,
                    depth, virtual_path, checksum, file_bucket_name,
                    file_object_key, markdown_bucket_name, markdown_object_key,
                    file_size, mime_type, line_count
                )
                VALUES (
                    %(knowledge_base_id)s, 'FILE', FALSE, %(name)s,
                    %(path_ltree)s::ltree, 1, %(virtual_path)s, %(checksum)s,
                    'source', %(source_key)s,
                    CASE WHEN %(status)s = 'complete' THEN 'markdown' END,
                    CASE WHEN %(status)s = 'complete' THEN %(markdown_key)s END,
                    12, 'text/plain',
                    CASE WHEN %(status)s = 'complete' THEN 1 END
                )
                RETURNING kid
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "name": f"{status}.txt",
                    "path_ltree": f"f1_{status}",
                    "virtual_path": f"/{status}.txt",
                    "checksum": f"checksum-{status}",
                    "source_key": f"{status}.txt",
                    "markdown_key": f"{status}.md",
                    "status": status,
                },
            )
            file_ids[status] = int((await cursor.fetchone())["kid"])

        for status, file_id in file_ids.items():
            await cursor.execute(
                """
                INSERT INTO knowledge_build_task (
                    knowledge_base_id, fs_entry_id, status, current_step,
                    error_message, started_at, finished_at
                )
                VALUES (
                    %(knowledge_base_id)s, %(file_id)s, %(status)s::varchar(32),
                    %(current_step)s, %(error_message)s,
                    NOW() - INTERVAL '2 minutes',
                    CASE
                        WHEN %(status)s::varchar(32) = 'running' THEN NULL
                        ELSE NOW()
                    END
                )
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "file_id": file_id,
                    "status": status,
                    "current_step": (
                        "complete" if status == "complete" else "vectorizing"
                    ),
                    "error_message": ("legacy failure" if status == "failed" else None),
                },
            )

        await cursor.execute(
            """
            INSERT INTO knowledge_chunk (
                fs_entry_id, chunk_no, start_line, end_line,
                chunk_text, search_text
            )
            VALUES (%(file_id)s, 1, 1, 1, 'legacy complete',
                    to_tsvector('legacy complete'))
            RETURNING kid, search_text
            """,
            {"file_id": file_ids["complete"]},
        )
        chunk = await cursor.fetchone()
        await cursor.execute(
            """
            INSERT INTO chunk_embedding_file_build_legacy_upgrade (
                chunk_id, embedding
            )
            VALUES (%(chunk_id)s, '[0.1,0.2,0.3]'::vector)
            """,
            {"chunk_id": int(chunk["kid"])},
        )
        await cursor.execute(
            """
            INSERT INTO knowledge_chunk_retrieval_mv (
                chunk_id, knowledge_base_id, fs_entry_id, full_path,
                chunk_no, start_line, end_line, chunk_text, search_text
            )
            VALUES (
                %(chunk_id)s, %(knowledge_base_id)s, %(file_id)s,
                'complete.txt', 1, 1, 1, 'legacy complete', %(search_text)s
            )
            """,
            {
                "chunk_id": int(chunk["kid"]),
                "knowledge_base_id": knowledge_base_id,
                "file_id": file_ids["complete"],
                "search_text": chunk["search_text"],
            },
        )
        await setup_connection.commit()
    finally:
        await setup_connection.close()

    try:
        upgrade_connection = await connection_factory()
        try:
            await _bootstrap().apply(upgrade_connection)
        finally:
            await upgrade_connection.close()

        verification_connection = await connection_factory()
        try:
            cursor = verification_connection.cursor()
            await cursor.execute(
                """
                SELECT fs_entry_id, status, current_stage, progress,
                       input_checksum, input_is_deleted, build_profile,
                       build_profile_hash, error_code, error_message,
                       failure_kind, outcome_uncertain, finished_at
                FROM knowledge_build_task
                WHERE knowledge_base_id = %(knowledge_base_id)s
                ORDER BY kid
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            rows = {int(row["fs_entry_id"]): row for row in await cursor.fetchall()}

            complete = rows[file_ids["complete"]]
            assert complete["status"] == "succeeded"
            assert complete["current_stage"] == "committing"
            assert complete["progress"] == 100
            assert complete["input_checksum"] == "checksum-complete"
            assert complete["input_is_deleted"] is False
            assert complete["build_profile"] == {"legacy": True, "profileVersion": 0}
            assert len(complete["build_profile_hash"]) == 64

            running = rows[file_ids["running"]]
            assert running["status"] == "failed"
            assert running["error_code"] == "MIGRATION_INTERRUPTED"
            assert running["failure_kind"] == "MIGRATION_INTERRUPTED"
            assert running["outcome_uncertain"] is True
            assert running["finished_at"] is not None

            failed = rows[file_ids["failed"]]
            assert failed["status"] == "failed"
            assert failed["error_code"] == "BUILD_FAILED"
            assert failed["error_message"] == "legacy failure"
            assert failed["input_checksum"] is None

            unsupported = rows[file_ids["unsupported"]]
            assert unsupported["status"] == "unsupported"
            assert unsupported["error_code"] == "UNSUPPORTED_FILE_TYPE"
            assert unsupported["input_checksum"] is None

            await cursor.execute(
                """
                SELECT fs.markdown_bucket_name, fs.markdown_object_key,
                       COUNT(DISTINCT chunk.kid) AS chunk_count,
                       COUNT(DISTINCT embedding.chunk_id) AS embedding_count,
                       COUNT(DISTINCT retrieval.chunk_id) AS retrieval_count
                FROM knowledge_fs_entry fs
                LEFT JOIN knowledge_chunk chunk ON chunk.fs_entry_id = fs.kid
                LEFT JOIN chunk_embedding_file_build_legacy_upgrade embedding
                  ON embedding.chunk_id = chunk.kid
                LEFT JOIN knowledge_chunk_retrieval_mv retrieval
                  ON retrieval.chunk_id = chunk.kid
                WHERE fs.kid = %(file_id)s
                GROUP BY fs.markdown_bucket_name, fs.markdown_object_key
                """,
                {"file_id": file_ids["complete"]},
            )
            artifact = await cursor.fetchone()
            assert artifact == {
                "markdown_bucket_name": "markdown",
                "markdown_object_key": "complete.md",
                "chunk_count": 1,
                "embedding_count": 1,
                "retrieval_count": 1,
            }

            await cursor.execute(
                """
                SELECT version, checksum, applied_at
                FROM knowledge_schema_migration
                WHERE split_part(version, '_', 1)::integer BETWEEN 38 AND 41
                ORDER BY version
                """
            )
            first_migration_rows = list(await cursor.fetchall())
            assert [
                row["version"].split("_", maxsplit=1)[0] for row in first_migration_rows
            ] == ["038", "039", "040", "041"]

            with pytest.raises(CheckViolation):
                await cursor.execute(
                    """
                    UPDATE knowledge_build_task
                    SET origin = 'INVALID'
                    WHERE fs_entry_id = %(file_id)s
                    """,
                    {"file_id": file_ids["complete"]},
                )
            await verification_connection.rollback()
        finally:
            await verification_connection.close()

        restart_connection = await connection_factory()
        try:
            await _bootstrap().apply(restart_connection)
            cursor = restart_connection.cursor()
            await cursor.execute(
                """
                SELECT version, checksum, applied_at
                FROM knowledge_schema_migration
                WHERE split_part(version, '_', 1)::integer BETWEEN 38 AND 41
                ORDER BY version
                """
            )
            assert list(await cursor.fetchall()) == first_migration_rows
            await cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM knowledge_build_task
                WHERE knowledge_base_id = %(knowledge_base_id)s
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            assert (await cursor.fetchone())["total"] == 4
        finally:
            await restart_connection.close()
    finally:
        cleanup_connection = await connection_factory()
        try:
            await cleanup_connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup_connection.commit()
        finally:
            await cleanup_connection.close()
