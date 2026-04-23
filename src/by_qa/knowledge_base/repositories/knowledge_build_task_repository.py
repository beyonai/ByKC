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
