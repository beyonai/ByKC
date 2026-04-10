"""Persistence helpers for knowledge_item rows."""

import json
from typing import Any


class KnowledgeItemRepository:
    """Repository for knowledge item metadata."""

    def upsert(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        item_code: str,
        item_kind: str,
        description: str | None,
        status: str,
        source_code: str,
        type_code: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Insert or update the knowledge item row."""
        cursor.execute(
            """
            MERGE INTO knowledge_item AS target
            USING (
                SELECT
                    %(knowledge_base_id)s AS knowledge_base_id,
                    %(fs_entry_id)s AS fs_entry_id,
                    %(item_code)s AS item_code,
                    %(item_kind)s AS item_kind,
                    %(description)s AS description,
                    %(source_code)s AS source_code,
                    %(type_code)s AS type_code,
                    %(status)s AS status,
                    %(metadata)s::jsonb AS metadata
            ) AS source
            ON (
                target.knowledge_base_id = source.knowledge_base_id
                AND target.fs_entry_id = source.fs_entry_id
            )
            WHEN MATCHED THEN
                UPDATE SET
                    item_code = source.item_code,
                    item_kind = source.item_kind,
                    description = source.description,
                    source_code = source.source_code,
                    type_code = source.type_code,
                    status = source.status,
                    metadata = source.metadata,
                    updated_at = NOW()
            WHEN NOT MATCHED THEN
                INSERT (
                    knowledge_base_id,
                    fs_entry_id,
                    item_code,
                    item_kind,
                    description,
                    source_code,
                    type_code,
                    status,
                    metadata,
                    created_at,
                    updated_at
                )
                VALUES (
                    source.knowledge_base_id,
                    source.fs_entry_id,
                    source.item_code,
                    source.item_kind,
                    source.description,
                    source.source_code,
                    source.type_code,
                    source.status,
                    source.metadata,
                    NOW(),
                    NOW()
                )
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "item_code": item_code,
                "item_kind": item_kind,
                "description": description,
                "source_code": source_code,
                "type_code": type_code,
                "status": status,
                "metadata": json.dumps(metadata or {}),
            },
        )
        cursor.execute(
            """
            SELECT kid, item_code
            FROM knowledge_item
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND fs_entry_id = %(fs_entry_id)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
            },
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def get_by_fs_entry_id(
        self, cursor: Any, *, knowledge_base_id: int, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Fetch a knowledge item by knowledge base and filesystem entry."""
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, fs_entry_id, item_code, current_version_id, status
            FROM knowledge_item
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND fs_entry_id = %(fs_entry_id)s
              AND is_deleted = FALSE
            """,
            {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def get_any_by_fs_entry_id(
        self, cursor: Any, *, knowledge_base_id: int, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Fetch a knowledge item by filesystem entry including logically deleted rows."""
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, fs_entry_id, item_code, current_version_id, status, is_deleted
            FROM knowledge_item
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND fs_entry_id = %(fs_entry_id)s
            """,
            {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def get_by_item_code(
        self, cursor: Any, *, knowledge_base_id: int, item_code: str
    ) -> dict[str, Any] | None:
        """Fetch a knowledge item by knowledge base and business item_code."""
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, fs_entry_id, item_code, item_kind, current_version_id, status, type_code, is_deleted
            FROM knowledge_item
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND item_code = %(item_code)s
              AND is_deleted = FALSE
            """,
            {"knowledge_base_id": knowledge_base_id, "item_code": item_code},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def get_any_by_item_code(
        self, cursor: Any, *, knowledge_base_id: int, item_code: str
    ) -> dict[str, Any] | None:
        """Fetch a knowledge item by business item_code including logically deleted rows."""
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, fs_entry_id, item_code, item_kind, current_version_id, status, type_code, is_deleted
            FROM knowledge_item
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND item_code = %(item_code)s
            """,
            {"knowledge_base_id": knowledge_base_id, "item_code": item_code},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def update_current_version(
        self, cursor: Any, *, knowledge_item_id: int, version_id: int
    ) -> None:
        """Update the current retrieval version pointer."""
        cursor.execute(
            """
            UPDATE knowledge_item
            SET current_version_id = %(version_id)s,
                updated_at = NOW()
            WHERE kid = %(knowledge_item_id)s
            """,
            {"knowledge_item_id": knowledge_item_id, "version_id": version_id},
        )

    def soft_delete_by_knowledge_base_id(
        self, cursor: Any, *, knowledge_base_id: int
    ) -> None:
        """Logically delete all knowledge items under one knowledge base."""
        cursor.execute(
            """
            UPDATE knowledge_item
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            {"knowledge_base_id": knowledge_base_id},
        )

    def soft_delete_by_item_code(
        self, cursor: Any, *, knowledge_base_id: int, item_code: str
    ) -> None:
        """Logically delete one knowledge item by business code."""
        cursor.execute(
            """
            UPDATE knowledge_item
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND item_code = %(item_code)s
            """,
            {"knowledge_base_id": knowledge_base_id, "item_code": item_code},
        )

    def soft_delete_by_fs_entry_ids(
        self, cursor: Any, *, knowledge_base_id: int, fs_entry_ids: list[int]
    ) -> None:
        """Logically delete knowledge items mapped to one filesystem subtree."""
        cursor.execute(
            """
            UPDATE knowledge_item
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND fs_entry_id = ANY(%(fs_entry_ids)s)
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_ids": fs_entry_ids,
            },
        )
