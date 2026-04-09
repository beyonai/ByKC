"""Persistence helpers for current-version retrieval projection rows."""

from typing import Any


class RetrievalProjectionRepository:
    """Repository for the current-version retrieval projection."""

    def refresh_for_item(self, cursor: Any, *, knowledge_item_id: int) -> None:
        """Rebuild current-version retrieval rows for one knowledge item."""
        cursor.execute(
            """
            DELETE FROM knowledge_item_chunk_retrieval_mv
            WHERE knowledge_item_id = %(knowledge_item_id)s
            """,
            {"knowledge_item_id": knowledge_item_id},
        )
        cursor.execute(
            """
            INSERT INTO knowledge_item_chunk_retrieval_mv (
                chunk_id,
                knowledge_base_id,
                kb_code,
                knowledge_base_status,
                fs_entry_id,
                parent_entry_id,
                item_code,
                item_kind,
                full_path,
                knowledge_item_id,
                knowledge_item_status,
                current_version_id,
                knowledge_item_version_id,
                version,
                source_code,
                type_code,
                metadata,
                chunk_no,
                start_line,
                end_line,
                chunk_text,
                search_text
            )
            SELECT
                c.kid,
                kb.kid,
                kb.kb_code,
                kb.status,
                fs.kid,
                fs.parent_entry_id,
                ki.item_code,
                ki.item_kind,
                (
                    WITH RECURSIVE item_path AS (
                        SELECT kid, parent_entry_id, name, depth, is_root
                        FROM knowledge_fs_entry
                        WHERE kid = fs.kid
                      UNION ALL
                        SELECT parent.kid, parent.parent_entry_id, parent.name, parent.depth, parent.is_root
                        FROM knowledge_fs_entry parent
                        JOIN item_path child ON child.parent_entry_id = parent.kid
                        WHERE parent.is_deleted = FALSE
                    )
                    SELECT COALESCE(string_agg(name, '/' ORDER BY depth), '')
                    FROM item_path
                    WHERE is_root = FALSE
                ) AS full_path,
                ki.kid,
                ki.status,
                ki.current_version_id,
                kv.kid,
                kv.version,
                ki.source_code,
                ki.type_code,
                ki.metadata,
                c.chunk_no,
                c.start_line,
                c.end_line,
                c.chunk_text,
                c.search_text
            FROM knowledge_item_chunk c
            JOIN knowledge_item ki ON ki.kid = c.knowledge_item_id
            JOIN knowledge_item_version kv ON kv.kid = c.knowledge_item_version_id
            JOIN knowledge_fs_entry fs ON fs.kid = ki.fs_entry_id
            JOIN knowledge_base kb ON kb.kid = ki.knowledge_base_id
            WHERE ki.kid = %(knowledge_item_id)s
              AND ki.current_version_id = kv.kid
              AND kb.is_deleted = FALSE
              AND fs.is_deleted = FALSE
              AND ki.is_deleted = FALSE
            """,
            {"knowledge_item_id": knowledge_item_id},
        )

    def delete_for_knowledge_base(self, cursor: Any, *, knowledge_base_id: int) -> None:
        """Delete retrieval projection rows for one knowledge base."""
        cursor.execute(
            """
            DELETE FROM knowledge_item_chunk_retrieval_mv
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            {"knowledge_base_id": knowledge_base_id},
        )

    def delete_for_item(self, cursor: Any, *, knowledge_item_id: int) -> None:
        """Delete retrieval projection rows for one knowledge item."""
        cursor.execute(
            """
            DELETE FROM knowledge_item_chunk_retrieval_mv
            WHERE knowledge_item_id = %(knowledge_item_id)s
            """,
            {"knowledge_item_id": knowledge_item_id},
        )
