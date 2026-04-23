"""Persistence helpers for knowledge_build_task rows."""

from __future__ import annotations

from typing import Any


class KnowledgeBuildTaskRepository:
    """Repository for file build task rows."""

    async def get_latest_by_fs_entry_id(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Fetch the latest build task for one file entry."""
        await cursor.execute(
            """
            SELECT
                kid,
                knowledge_base_id,
                fs_entry_id,
                status,
                current_step,
                error_message,
                started_at,
                finished_at,
                created_at,
                updated_at
            FROM knowledge_build_task
            WHERE fs_entry_id = %(fs_entry_id)s
            ORDER BY created_at DESC, kid DESC
            LIMIT 1
            """,
            {"fs_entry_id": fs_entry_id},
        )
        return await cursor.fetchone()

    async def create_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        status: str,
        current_step: str | None,
    ) -> dict[str, Any] | None:
        """Create one build task row for a file."""
        await cursor.execute(
            """
            INSERT INTO knowledge_build_task (
                knowledge_base_id,
                fs_entry_id,
                status,
                current_step,
                started_at,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(status)s,
                %(current_step)s,
                NOW(),
                NOW(),
                NOW()
            )
            RETURNING
                kid,
                knowledge_base_id,
                fs_entry_id,
                status,
                current_step,
                error_message,
                started_at,
                finished_at,
                created_at,
                updated_at
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "status": status,
                "current_step": current_step,
            },
        )
        return await cursor.fetchone()

    async def update_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        status: str | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        """Update task state fields for one build task row."""
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = COALESCE(%(status)s, status),
                current_step = COALESCE(%(current_step)s, current_step),
                error_message = %(error_message)s,
                finished_at = CASE
                    WHEN %(finished)s THEN NOW()
                    ELSE finished_at
                END,
                updated_at = NOW()
            WHERE kid = %(task_id)s
            """,
            {
                "task_id": task_id,
                "status": status,
                "current_step": current_step,
                "error_message": error_message,
                "finished": finished,
            },
        )
