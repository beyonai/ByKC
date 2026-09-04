"""Set-based acceptance for directory and single-file build batches."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


class KnowledgeBuildAcceptanceRepository:
    """Classify a stable file snapshot and create one durable Build batch."""

    _TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

    def __init__(self, embedding_table_name: str):
        if not self._TABLE_NAME.fullmatch(embedding_table_name):
            raise ValueError("invalid embedding table name")
        self.embedding_table_name = embedding_table_name

    async def accept(
        self,
        cursor: Any,
        *,
        batch_id: str,
        knowledge_base_id: int,
        scope: str,
        target_path_snapshot: str,
        target_fs_entry_id: int | None,
        target_path_ltree: str | None,
        build_profile: Mapping[str, Any],
        build_profile_hash: str,
        force: bool,
        priority: int,
        preview_limit: int = 20,
    ) -> dict[str, Any]:
        """Accept one request without materializing its candidates in Python."""
        if scope not in {"SINGLE_FILE", "DIRECTORY"}:
            raise ValueError(f"unsupported file build scope: {scope}")
        if scope == "SINGLE_FILE" and target_fs_entry_id is None:
            raise ValueError("single-file acceptance requires target_fs_entry_id")
        if scope == "DIRECTORY" and target_fs_entry_id is not None:
            if not target_path_ltree:
                raise ValueError("directory acceptance requires target_path_ltree")
        if preview_limit < 0:
            raise ValueError("preview_limit must be non-negative")

        params = {
            "batch_id": batch_id,
            "knowledge_base_id": knowledge_base_id,
            "scope": scope,
            "target_path_snapshot": target_path_snapshot,
            "target_fs_entry_id": target_fs_entry_id,
            "target_path_ltree": target_path_ltree,
            "build_profile": self._canonical_json(build_profile),
            "build_profile_hash": build_profile_hash,
            "force": force,
            "priority": priority,
            "preview_limit": preview_limit,
        }
        # Overlapping requests acquire file-scoped locks in stable order. This
        # closes the read-then-insert race without loading file IDs in Python.
        await cursor.execute(
            """
            SELECT pg_advisory_xact_lock(fs.kid)
            FROM knowledge_fs_entry fs
            WHERE fs.knowledge_base_id = %(knowledge_base_id)s
              AND fs.entry_type = 'FILE'
              AND fs.is_deleted = FALSE
              AND (
                  (%(scope)s = 'SINGLE_FILE' AND fs.kid = %(target_fs_entry_id)s)
                  OR (
                      %(scope)s = 'DIRECTORY'
                      AND (
                          %(target_fs_entry_id)s IS NULL
                          OR fs.path_ltree <@ %(target_path_ltree)s::ltree
                      )
                  )
              )
            ORDER BY fs.kid
            """,
            params,
        )
        await cursor.execute(
            f"""
            CREATE TEMP TABLE file_build_acceptance_snapshot ON COMMIT DELETE ROWS AS
            WITH candidates AS (
                SELECT
                    fs.kid AS fs_entry_id,
                    fs.virtual_path AS file_path_snapshot,
                    fs.checksum AS input_checksum,
                    fs.is_deleted AS input_is_deleted,
                    fs.file_bucket_name,
                    fs.file_object_key,
                    fs.markdown_bucket_name,
                    fs.markdown_object_key
                FROM knowledge_fs_entry fs
                WHERE fs.knowledge_base_id = %(knowledge_base_id)s
                  AND fs.entry_type = 'FILE'
                  AND fs.is_deleted = FALSE
                  AND (
                      (
                          %(scope)s = 'SINGLE_FILE'
                          AND fs.kid = %(target_fs_entry_id)s
                      )
                      OR (
                          %(scope)s = 'DIRECTORY'
                          AND (
                              %(target_fs_entry_id)s IS NULL
                              OR fs.path_ltree <@ %(target_path_ltree)s::ltree
                          )
                      )
                  )
            )
            SELECT
                candidate.*,
                active.kid AS active_task_id,
                active.batch_id AS active_batch_id,
                reusable.kid AS reusable_task_id,
                reusable.status AS reusable_status,
                CASE
                    WHEN candidate.input_checksum IS NULL
                      OR candidate.file_bucket_name IS NULL
                      OR candidate.file_object_key IS NULL
                        THEN 'SKIPPED'
                    WHEN reusable.kid IS NOT NULL THEN 'REUSED'
                    ELSE 'ACCEPTED'
                END AS acceptance
            FROM candidates candidate
            LEFT JOIN LATERAL (
                SELECT task.kid, task.batch_id
                FROM knowledge_build_task task
                WHERE task.fs_entry_id = candidate.fs_entry_id
                  AND task.status IN ('pending', 'running')
                ORDER BY task.created_at DESC, task.kid DESC
                LIMIT 1
            ) active ON TRUE
            LEFT JOIN LATERAL (
                SELECT task.kid, task.status
                FROM knowledge_build_task task
                WHERE task.fs_entry_id = candidate.fs_entry_id
                  AND task.input_checksum = candidate.input_checksum
                  AND task.input_is_deleted = candidate.input_is_deleted
                  AND task.build_profile_hash = %(build_profile_hash)s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM knowledge_file_update_timeline update_event
                      WHERE update_event.fs_entry_id = task.fs_entry_id
                        AND update_event.created_at > task.created_at
                        AND update_event.old_checksum IS DISTINCT FROM
                            update_event.new_checksum
                  )
                  AND (
                      task.status IN ('pending', 'running')
                      OR (
                          %(force)s = FALSE
                          AND task.status = 'unsupported'
                      )
                      OR (
                          %(force)s = FALSE
                          AND task.status = 'succeeded'
                          AND candidate.markdown_bucket_name IS NOT NULL
                          AND candidate.markdown_object_key IS NOT NULL
                          AND EXISTS (
                              SELECT 1 FROM knowledge_chunk chunk
                              WHERE chunk.fs_entry_id = candidate.fs_entry_id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM knowledge_chunk chunk
                              LEFT JOIN {self.embedding_table_name} embedding
                                ON embedding.chunk_id = chunk.kid
                              LEFT JOIN knowledge_chunk_retrieval_mv retrieval
                                ON retrieval.chunk_id = chunk.kid
                              WHERE chunk.fs_entry_id = candidate.fs_entry_id
                                AND (
                                    embedding.chunk_id IS NULL
                                    OR retrieval.chunk_id IS NULL
                                )
                          )
                      )
                  )
                ORDER BY task.created_at DESC, task.kid DESC
                LIMIT 1
            ) reusable ON TRUE
            """,
            params,
        )
        await cursor.execute(
            """
            INSERT INTO knowledge_build_batch (
                batch_id, knowledge_base_id, task_type, scope,
                target_path_snapshot, status, candidate_count, eligible_count,
                accepted_count, reused_count, acceptance_skipped_count,
                completed_count, version, completed_at, created_at, updated_at
            )
            SELECT
                %(batch_id)s, %(knowledge_base_id)s, 'FILE_BUILD', %(scope)s,
                %(target_path_snapshot)s,
                CASE WHEN counts.accepted_count = 0 THEN 'completed' ELSE 'pending' END,
                counts.candidate_count,
                counts.accepted_count + counts.reused_count,
                counts.accepted_count,
                counts.reused_count,
                counts.skipped_count,
                0,
                0,
                CASE WHEN counts.accepted_count = 0 THEN NOW() ELSE NULL END,
                NOW(),
                NOW()
            FROM (
                SELECT
                    COUNT(*) AS candidate_count,
                    COUNT(*) FILTER (WHERE acceptance = 'ACCEPTED') AS accepted_count,
                    COUNT(*) FILTER (WHERE acceptance = 'REUSED') AS reused_count,
                    COUNT(*) FILTER (WHERE acceptance = 'SKIPPED') AS skipped_count
                FROM file_build_acceptance_snapshot
            ) counts
            RETURNING *
            """,
            params,
        )
        batch = await cursor.fetchone()

        await cursor.execute(
            """
            UPDATE knowledge_build_task task
            SET status = 'skipped',
                progress = 100,
                error_code = 'SUPERSEDED',
                error_message = 'Build superseded by a newer request',
                outcome_uncertain = (task.status = 'running'),
                worker_id = NULL,
                lease_token = NULL,
                heartbeat_at = NULL,
                lease_expires_at = NULL,
                finished_at = NOW(),
                updated_at = NOW()
            FROM file_build_acceptance_snapshot snapshot
            WHERE snapshot.acceptance = 'ACCEPTED'
              AND snapshot.active_task_id = task.kid
              AND task.status IN ('pending', 'running')
            RETURNING task.*
            """
        )
        superseded_tasks = list(await cursor.fetchall())
        await cursor.execute(
            """
            CREATE TEMP TABLE file_build_superseded_batches ON COMMIT DELETE ROWS AS
            SELECT task.batch_id, COUNT(*) AS completed_delta
            FROM file_build_acceptance_snapshot snapshot
            JOIN knowledge_build_task task ON task.kid = snapshot.active_task_id
            WHERE snapshot.acceptance = 'ACCEPTED'
              AND task.batch_id IS NOT NULL
              AND task.status = 'skipped'
              AND task.error_code = 'SUPERSEDED'
            GROUP BY task.batch_id
            """
        )
        await cursor.execute(
            """
            UPDATE knowledge_build_batch batch
            SET completed_count = batch.completed_count + source.completed_delta,
                version = batch.version + source.completed_delta,
                status = CASE
                    WHEN batch.completed_count + source.completed_delta
                         = batch.accepted_count
                        THEN 'completed'
                    ELSE 'processing'
                END,
                completed_at = CASE
                    WHEN batch.completed_count + source.completed_delta
                         = batch.accepted_count
                        THEN COALESCE(batch.completed_at, NOW())
                    ELSE batch.completed_at
                END,
                updated_at = NOW()
            FROM file_build_superseded_batches source
            WHERE batch.batch_id = source.batch_id
            RETURNING batch.*
            """
        )
        completed_superseded_batches = [
            row
            for row in await cursor.fetchall()
            if str(row.get("status")) == "completed"
        ]

        await cursor.execute(
            """
            INSERT INTO knowledge_build_task (
                knowledge_base_id, fs_entry_id, batch_id, origin,
                execution_mode, parent_semantic_task_id, file_path_snapshot,
                input_checksum, input_is_deleted, build_profile,
                build_profile_hash, status, current_step, current_stage,
                progress, priority, outcome_uncertain, created_at, updated_at
            )
            SELECT
                %(knowledge_base_id)s,
                snapshot.fs_entry_id,
                %(batch_id)s,
                'API',
                'BACKGROUND',
                NULL,
                snapshot.file_path_snapshot,
                snapshot.input_checksum,
                snapshot.input_is_deleted,
                %(build_profile)s::jsonb,
                %(build_profile_hash)s,
                'pending',
                'markdown',
                'accepted',
                0,
                %(priority)s,
                FALSE,
                NOW(),
                NOW()
            FROM file_build_acceptance_snapshot snapshot
            WHERE snapshot.acceptance = 'ACCEPTED'
            """,
            params,
        )
        await cursor.execute(
            """
            SELECT *
            FROM (
                SELECT
                    task.kid AS task_id,
                    task.fs_entry_id,
                    task.file_path_snapshot,
                    task.status,
                    FALSE AS reused
                FROM knowledge_build_task task
                WHERE task.batch_id = %(batch_id)s
                UNION ALL
                SELECT
                    snapshot.reusable_task_id AS task_id,
                    snapshot.fs_entry_id,
                    snapshot.file_path_snapshot,
                    snapshot.reusable_status AS status,
                    TRUE AS reused
                FROM file_build_acceptance_snapshot snapshot
                WHERE snapshot.acceptance = 'REUSED'
            ) preview
            ORDER BY fs_entry_id, task_id
            LIMIT %(preview_limit)s
            """,
            params,
        )
        preview = list(await cursor.fetchall())
        return {
            "batch": batch,
            "preview": preview,
            "superseded_tasks": superseded_tasks,
            "completed_superseded_batches": completed_superseded_batches,
        }

    @staticmethod
    def _canonical_json(value: Mapping[str, Any]) -> str:
        import json

        return json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
