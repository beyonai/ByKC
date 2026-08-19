"""Persistence helpers for KnowledgeEntity processing batches."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class KnowledgeSemanticProcessingBatchRepository:
    """Repository for batch progress shared by per-file semantic tasks."""

    async def create_batch(
        self,
        cursor: Any,
        *,
        batch_id: str,
        knowledge_base_id: int,
        task_type: str,
        scope: str,
        total_count: int,
        completed_count: int = 0,
        version: int = 0,
        extra_params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if total_count < 0:
            raise ValueError("total_count must be greater than or equal to 0")
        if completed_count < 0 or completed_count > total_count:
            raise ValueError("completed_count must be between 0 and total_count")
        if version < 0:
            raise ValueError("version must be greater than or equal to 0")
        completed = completed_count == total_count
        await cursor.execute(
            """
            INSERT INTO knowledge_semantic_processing_batch (
                batch_id,
                knowledge_base_id,
                task_type,
                scope,
                status,
                total_count,
                completed_count,
                version,
                extra_params,
                completed_at,
                created_at,
                updated_at
            )
            VALUES (
                %(batch_id)s,
                %(knowledge_base_id)s,
                %(task_type)s,
                %(scope)s,
                %(status)s,
                %(total_count)s,
                %(completed_count)s,
                %(version)s,
                %(extra_params)s::jsonb,
                CASE WHEN %(completed)s THEN NOW() ELSE NULL END,
                NOW(),
                NOW()
            )
            RETURNING *
            """,
            {
                "batch_id": batch_id,
                "knowledge_base_id": knowledge_base_id,
                "task_type": task_type.strip().upper(),
                "scope": scope.strip().upper(),
                "status": "completed" if completed else "processing",
                "total_count": total_count,
                "completed_count": completed_count,
                "version": version,
                "extra_params": self._json_value(extra_params),
                "completed": completed,
            },
        )
        return await cursor.fetchone()

    async def advance_batch(
        self,
        cursor: Any,
        *,
        batch_id: str,
        completed_delta: int = 1,
    ) -> dict[str, Any] | None:
        """Advance terminal progress while serializing concurrent completions."""
        if completed_delta < 1:
            raise ValueError("completed_delta must be greater than 0")
        await cursor.execute(
            """
            UPDATE knowledge_semantic_processing_batch
            SET completed_count = completed_count + %(completed_delta)s,
                version = version + %(completed_delta)s,
                status = CASE
                    WHEN completed_count + %(completed_delta)s = total_count
                        THEN 'completed'
                    ELSE status
                END,
                completed_at = CASE
                    WHEN completed_count + %(completed_delta)s = total_count
                        THEN COALESCE(completed_at, NOW())
                    ELSE completed_at
                END,
                updated_at = NOW()
            WHERE batch_id = %(batch_id)s
              AND status = 'processing'
              AND completed_count + %(completed_delta)s <= total_count
            RETURNING *
            """,
            {"batch_id": batch_id, "completed_delta": completed_delta},
        )
        return await cursor.fetchone()

    async def get_batch(
        self, cursor: Any, *, batch_id: str, knowledge_base_id: int | None = None
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_semantic_processing_batch
            WHERE batch_id = %(batch_id)s
              AND (
                    %(knowledge_base_id)s IS NULL
                    OR knowledge_base_id = %(knowledge_base_id)s
              )
            """,
            {"batch_id": batch_id, "knowledge_base_id": knowledge_base_id},
        )
        return await cursor.fetchone()

    async def count_tasks_by_status(
        self, cursor: Any, *, batch_id: str
    ) -> dict[str, int]:
        await cursor.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM knowledge_semantic_processing_task
            WHERE batch_id = %(batch_id)s
            GROUP BY status
            """,
            {"batch_id": batch_id},
        )
        rows = await cursor.fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def _json_value(self, value: Mapping[str, Any] | None) -> str:
        return json.dumps(dict(value or {}), ensure_ascii=False)
