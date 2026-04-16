"""Persistence helpers for current-version retrieval projection rows."""

from typing import Any


class RetrievalProjectionRepository:
    """Repository for the current-version retrieval projection."""

    async def delete_for_fs_entry_ids(
        self, cursor: Any, *, knowledge_base_id: int, fs_entry_ids: list[int]
    ) -> None:
        """Delete retrieval projection rows for one filesystem subtree."""
        await cursor.execute(
            """
            DELETE FROM knowledge_item_chunk_retrieval_mv
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND fs_entry_id = ANY(%(fs_entry_ids)s)
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_ids": fs_entry_ids,
            },
        )

    async def refresh_for_fs_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        full_path: str,
    ) -> None:
        """Rebuild retrieval projection rows for one file entry."""
        await cursor.execute(
            """
            DELETE FROM knowledge_chunk_retrieval_mv
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND fs_entry_id = %(fs_entry_id)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
            },
        )
        await cursor.execute(
            """
            INSERT INTO knowledge_chunk_retrieval_mv (
                chunk_id,
                knowledge_base_id,
                fs_entry_id,
                full_path,
                chunk_no,
                start_line,
                end_line,
                chunk_text,
                search_text
            )
            SELECT
                c.kid,
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(full_path)s,
                c.chunk_no,
                c.start_line,
                c.end_line,
                c.chunk_text,
                c.search_text
            FROM knowledge_chunk c
            WHERE c.fs_entry_id = %(fs_entry_id)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "full_path": full_path,
            },
        )
