"""Exercise the actual acceptance SELECT with synthetic, read-only fixtures."""

from __future__ import annotations

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from by_qa.config import get_settings
from by_qa.knowledge_base.repositories.knowledge_build_acceptance_repository import (
    KnowledgeBuildAcceptanceRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class SnapshotCaptured(Exception):
    pass


class SnapshotCursor:
    async def execute(self, query, params=None):
        if "CREATE TEMP TABLE file_build_acceptance_snapshot" in query:
            self.query = query[query.index("WITH candidates") :]
            self.params = params
            raise SnapshotCaptured


@pytest.mark.parametrize(
    ("force", "has_source", "expected_acceptance", "expected_reuse"),
    [
        (False, True, "REUSED", 12),
        (True, True, "ACCEPTED", None),
        (False, False, "SKIPPED", 12),
    ],
)
async def test_snapshot_filters_before_ranking_and_preserves_unmatched_files(
    force, has_source, expected_acceptance, expected_reuse
):
    cursor = SnapshotCursor()
    with pytest.raises(SnapshotCaptured):
        await KnowledgeBuildAcceptanceRepository("fixture_embedding").accept(
            cursor,
            batch_id="fixture",
            knowledge_base_id=1,
            scope="DIRECTORY",
            target_path_snapshot="/",
            target_fs_entry_id=None,
            target_path_ltree=None,
            build_profile={},
            build_profile_hash="current",
            force=force,
            priority=0,
        )
    # CTEs shadow every business table. This test never creates or modifies rows.
    # Equal timestamps require kid ordering; newer mismatching tasks must not
    # hide an older reusable result. File 2 has no history and must survive joins.
    fixtures = """
        WITH knowledge_fs_entry AS (
            SELECT id::bigint AS kid, 1 AS knowledge_base_id,
                'FILE'::text AS entry_type, FALSE AS is_deleted,
                'file'::ltree AS path_ltree, '/file'::text AS virtual_path,
                'sha'::text AS checksum,
                CASE WHEN %(has_source)s THEN 'source'::text END AS file_bucket_name,
                'key'::text AS file_object_key,
                NULL::text AS markdown_bucket_name,
                NULL::text AS markdown_object_key
            FROM (SELECT 1 AS id UNION ALL SELECT 2 AS id) files
        ), knowledge_build_task AS (
            SELECT kid::bigint, 1::bigint AS fs_entry_id, batch_id::text,
                status::text, checksum::text AS input_checksum,
                FALSE AS input_is_deleted, profile::text AS build_profile_hash,
                created_at::timestamp
            FROM (VALUES
                (11, 'old', 'unsupported', 'sha', 'current', '2026-01-01'),
                (12, 'old', 'unsupported', 'sha', 'current', '2026-01-01'),
                (13, 'old', 'unsupported', 'other', 'current', '2026-01-02'),
                (14, 'old', 'failed', 'sha', 'current', '2026-01-03'),
                (15, 'active', 'running', 'sha', 'other', '2026-01-04')
            ) tasks(kid, batch_id, status, checksum, profile, created_at)
        ), knowledge_file_update_timeline AS (
            SELECT NULL::bigint AS fs_entry_id, NULL::timestamp AS created_at,
                NULL::text AS old_checksum, NULL::text AS new_checksum WHERE FALSE
        ), knowledge_chunk AS (
            SELECT NULL::bigint AS kid, NULL::bigint AS fs_entry_id WHERE FALSE
        ), fixture_embedding AS (
            SELECT NULL::bigint AS chunk_id WHERE FALSE
        ), knowledge_chunk_retrieval_mv AS (
            SELECT NULL::bigint AS chunk_id WHERE FALSE
        ), candidates"""
    query = cursor.query.replace("WITH candidates", fixtures, 1)
    connection = await AsyncConnection.connect(
        get_settings().resolved_kb_opengauss_dsn,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )
    try:
        result = await connection.execute(
            query, {**cursor.params, "has_source": has_source}
        )
        rows = {row["fs_entry_id"]: row for row in await result.fetchall()}
        assert len(rows) == 2
        assert rows[1]["active_task_id"] == 15
        assert rows[1]["active_batch_id"] == "active"
        assert rows[1]["reusable_task_id"] == expected_reuse
        assert rows[1]["acceptance"] == expected_acceptance
        assert rows[2]["active_task_id"] is None
        assert rows[2]["reusable_task_id"] is None
        assert rows[2]["acceptance"] == ("ACCEPTED" if has_source else "SKIPPED")
    finally:
        await connection.rollback()
        await connection.close()
