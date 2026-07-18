"""Persistence helpers for knowledge file update timeline rows."""

from __future__ import annotations

from typing import Any


class KnowledgeFileUpdateTimelineRepository:
    """Repository for immutable file-update events and LLM summary backfills."""

    async def create_update_event(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        old_checksum: str | None,
        new_checksum: str,
        old_file_size: int | None,
        new_file_size: int,
        summary: str,
        summary_source: str,
    ) -> dict[str, Any] | None:
        """Insert one update event and return its persisted row."""
        await cursor.execute(
            """
            INSERT INTO knowledge_file_update_timeline (
                knowledge_base_id,
                fs_entry_id,
                old_checksum,
                new_checksum,
                old_file_size,
                new_file_size,
                summary,
                summary_source,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(old_checksum)s,
                %(new_checksum)s,
                %(old_file_size)s,
                %(new_file_size)s,
                %(summary)s,
                %(summary_source)s,
                NOW(),
                NOW()
            )
            RETURNING
                kid,
                knowledge_base_id,
                fs_entry_id,
                event_type,
                old_checksum,
                new_checksum,
                old_file_size,
                new_file_size,
                summary,
                summary_source,
                created_at,
                updated_at
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "old_checksum": old_checksum,
                "new_checksum": new_checksum,
                "old_file_size": old_file_size,
                "new_file_size": new_file_size,
                "summary": summary,
                "summary_source": summary_source,
            },
        )
        return await cursor.fetchone()

    async def update_summary_from_llm(
        self, cursor: Any, *, timeline_id: int, summary: str
    ) -> dict[str, Any] | None:
        """Backfill a timeline event summary from an LLM."""
        await cursor.execute(
            """
            UPDATE knowledge_file_update_timeline
            SET summary = %(summary)s,
                summary_source = 'LLM',
                updated_at = NOW()
            WHERE kid = %(timeline_id)s
            RETURNING
                kid,
                knowledge_base_id,
                fs_entry_id,
                event_type,
                old_checksum,
                new_checksum,
                old_file_size,
                new_file_size,
                summary,
                summary_source,
                created_at,
                updated_at
            """,
            {"timeline_id": timeline_id, "summary": summary},
        )
        return await cursor.fetchone()
