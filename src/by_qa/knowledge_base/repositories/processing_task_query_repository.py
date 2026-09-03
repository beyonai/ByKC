"""Unified read model across the isolated Build and Semantic task pools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class ProcessingTaskQueryRepository:
    """Page task history without merging independently paged results in Python."""

    async def query(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        task_id: int | None,
        fs_entry_id: int | None,
        batch_id: str | None,
        task_type: str | None,
        statuses: Sequence[str] | None,
        latest_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        params = {
            "knowledge_base_id": knowledge_base_id,
            "task_id": task_id,
            "fs_entry_id": fs_entry_id,
            "batch_id": batch_id,
            "task_type": task_type,
            "statuses": list(statuses or []),
            "latest_only": latest_only,
            "limit": limit,
            "offset": offset,
        }
        cte = self._all_tasks_cte()
        filters = """
            (%(task_id)s::bigint IS NULL OR kid = %(task_id)s::bigint)
            AND (
                %(fs_entry_id)s::bigint IS NULL
                OR fs_entry_id = %(fs_entry_id)s::bigint
            )
            AND (%(batch_id)s::text IS NULL OR batch_id = %(batch_id)s::text)
            AND (%(task_type)s::text IS NULL OR task_type = %(task_type)s::text)
            AND (
                COALESCE(array_length(%(statuses)s::text[], 1), 0) = 0
                OR status = ANY(%(statuses)s::text[])
            )
        """
        await cursor.execute(
            f"""
            {cte}, ranked AS (
                SELECT all_tasks.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY fs_entry_id, task_type
                        ORDER BY created_at DESC, kid DESC
                    ) AS task_rank
                FROM all_tasks
                WHERE {filters}
            )
            SELECT COUNT(*) AS total
            FROM ranked
            WHERE %(latest_only)s = FALSE OR task_rank = 1
            """,
            params,
        )
        count_row = await cursor.fetchone()
        await cursor.execute(
            f"""
            {cte}, ranked AS (
                SELECT all_tasks.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY fs_entry_id, task_type
                        ORDER BY created_at DESC, kid DESC
                    ) AS task_rank
                FROM all_tasks
                WHERE {filters}
            )
            SELECT *
            FROM ranked
            WHERE %(latest_only)s = FALSE OR task_rank = 1
            ORDER BY created_at DESC, task_type, kid DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return int(count_row["total"]), list(await cursor.fetchall())

    @staticmethod
    def _all_tasks_cte() -> str:
        return """
            WITH all_tasks AS (
                SELECT
                    task.kid, task.knowledge_base_id, task.fs_entry_id,
                    task.batch_id, 'FILE_BUILD'::text AS task_type,
                    task.file_path_snapshot, task.status, task.current_stage,
                    task.progress, task.input_checksum, NULL::text AS index_version,
                    task.result_payload, task.error_code, task.error_message,
                    task.outcome_uncertain, task.origin, task.execution_mode,
                    task.parent_semantic_task_id, task.input_is_deleted,
                    task.build_profile, task.build_profile_hash,
                    task.created_at, task.started_at, task.finished_at
                FROM knowledge_build_task task
                WHERE task.knowledge_base_id = %(knowledge_base_id)s
                UNION ALL
                SELECT
                    task.kid, task.knowledge_base_id, task.fs_entry_id,
                    task.batch_id, task.task_type::text,
                    task.file_path_snapshot, task.status, task.current_stage,
                    task.progress, task.input_checksum, task.index_version,
                    task.result_payload, task.error_code, task.error_message,
                    task.outcome_uncertain, NULL::text AS origin,
                    NULL::text AS execution_mode,
                    NULL::bigint AS parent_semantic_task_id,
                    NULL::boolean AS input_is_deleted,
                    NULL::jsonb AS build_profile,
                    NULL::varchar(64) AS build_profile_hash,
                    task.created_at, task.started_at, task.finished_at
                FROM knowledge_semantic_processing_task task
                WHERE task.knowledge_base_id = %(knowledge_base_id)s
            )
        """
