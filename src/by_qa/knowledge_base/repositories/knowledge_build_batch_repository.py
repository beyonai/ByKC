"""Persistence helpers for durable file-build batches."""

from __future__ import annotations

from typing import Any


class KnowledgeBuildBatchRepository:
    """Create and aggregate batches owned by external file-build requests."""

    async def create_batch(
        self,
        cursor: Any,
        *,
        batch_id: str,
        knowledge_base_id: int,
        scope: str,
        target_path_snapshot: str,
        candidate_count: int,
        eligible_count: int,
        accepted_count: int,
        reused_count: int,
        acceptance_skipped_count: int,
    ) -> dict[str, Any] | None:
        """Persist one fully counted acceptance result."""
        self._validate_counts(
            candidate_count=candidate_count,
            eligible_count=eligible_count,
            accepted_count=accepted_count,
            reused_count=reused_count,
            acceptance_skipped_count=acceptance_skipped_count,
        )
        normalized_scope = scope.strip().upper()
        if normalized_scope not in {"SINGLE_FILE", "DIRECTORY"}:
            raise ValueError(f"unsupported file build scope: {scope}")
        status = "completed" if accepted_count == 0 else "pending"
        await cursor.execute(
            """
            INSERT INTO knowledge_build_batch (
                batch_id,
                knowledge_base_id,
                task_type,
                scope,
                target_path_snapshot,
                status,
                candidate_count,
                eligible_count,
                accepted_count,
                reused_count,
                acceptance_skipped_count,
                completed_count,
                version,
                completed_at,
                created_at,
                updated_at
            )
            VALUES (
                %(batch_id)s,
                %(knowledge_base_id)s,
                'FILE_BUILD',
                %(scope)s,
                %(target_path_snapshot)s,
                %(status)s,
                %(candidate_count)s,
                %(eligible_count)s,
                %(accepted_count)s,
                %(reused_count)s,
                %(acceptance_skipped_count)s,
                0,
                0,
                CASE WHEN %(completed)s THEN NOW() ELSE NULL END,
                NOW(),
                NOW()
            )
            RETURNING *
            """,
            {
                "batch_id": batch_id,
                "knowledge_base_id": knowledge_base_id,
                "scope": normalized_scope,
                "target_path_snapshot": target_path_snapshot,
                "status": status,
                "candidate_count": candidate_count,
                "eligible_count": eligible_count,
                "accepted_count": accepted_count,
                "reused_count": reused_count,
                "acceptance_skipped_count": acceptance_skipped_count,
                "completed": accepted_count == 0,
            },
        )
        return await cursor.fetchone()

    async def mark_processing(
        self, cursor: Any, *, batch_id: str
    ) -> dict[str, Any] | None:
        """Move a non-empty batch to processing when its first task is claimed."""
        await cursor.execute(
            """
            UPDATE knowledge_build_batch
            SET status = 'processing',
                version = version + 1,
                updated_at = NOW()
            WHERE batch_id = %(batch_id)s
              AND status = 'pending'
              AND accepted_count > 0
            RETURNING *
            """,
            {"batch_id": batch_id},
        )
        return await cursor.fetchone()

    async def advance_batch(
        self,
        cursor: Any,
        *,
        batch_id: str,
        completed_delta: int = 1,
    ) -> dict[str, Any] | None:
        """Advance terminal task progress with a serialized SQL update."""
        if completed_delta < 1:
            raise ValueError("completed_delta must be greater than 0")
        await cursor.execute(
            """
            UPDATE knowledge_build_batch
            SET completed_count = completed_count + %(completed_delta)s,
                version = version + %(completed_delta)s,
                status = CASE
                    WHEN completed_count + %(completed_delta)s = accepted_count
                        THEN 'completed'
                    ELSE 'processing'
                END,
                completed_at = CASE
                    WHEN completed_count + %(completed_delta)s = accepted_count
                        THEN COALESCE(completed_at, NOW())
                    ELSE completed_at
                END,
                updated_at = NOW()
            WHERE batch_id = %(batch_id)s
              AND status IN ('pending', 'processing')
              AND completed_count + %(completed_delta)s <= accepted_count
            RETURNING *
            """,
            {"batch_id": batch_id, "completed_delta": completed_delta},
        )
        return await cursor.fetchone()

    async def get_batch(
        self,
        cursor: Any,
        *,
        batch_id: str,
        knowledge_base_id: int | None = None,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_build_batch
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
            FROM knowledge_build_task
            WHERE batch_id = %(batch_id)s
            GROUP BY status
            """,
            {"batch_id": batch_id},
        )
        rows = await cursor.fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    @staticmethod
    def _validate_counts(
        *,
        candidate_count: int,
        eligible_count: int,
        accepted_count: int,
        reused_count: int,
        acceptance_skipped_count: int,
    ) -> None:
        values = (
            candidate_count,
            eligible_count,
            accepted_count,
            reused_count,
            acceptance_skipped_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("file build batch counts must be non-negative")
        if candidate_count != (
            accepted_count + reused_count + acceptance_skipped_count
        ):
            raise ValueError("candidate_count invariant is not satisfied")
        if eligible_count != accepted_count + reused_count:
            raise ValueError("eligible_count invariant is not satisfied")
