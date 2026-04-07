"""Persistence helpers for knowledge_base rows."""

import json
from typing import Any


class KnowledgeBaseRepository:
    """Repository for knowledge base metadata."""

    def get_any_by_code(self, cursor: Any, kb_code: str) -> dict[str, Any] | None:
        """Fetch a knowledge base by business code including logically deleted rows."""
        cursor.execute(
            """
            SELECT kid, kb_code, kb_name, kb_description, status, is_deleted, root_entry_id
            FROM knowledge_base
            WHERE kb_code = %(kb_code)s
            """,
            {"kb_code": kb_code},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def create_knowledge_base(
        self,
        cursor: Any,
        *,
        kb_code: str,
        kb_name: str,
        kb_description: str | None,
        status: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        """Insert a knowledge base."""
        cursor.execute(
            """
            INSERT INTO knowledge_base (
                kb_code,
                kb_name,
                kb_description,
                status,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                %(kb_code)s,
                %(kb_name)s,
                %(kb_description)s,
                %(status)s,
                %(metadata)s::jsonb,
                NOW(),
                NOW()
            )
            """,
            {
                "kb_code": kb_code,
                "kb_name": kb_name,
                "kb_description": kb_description,
                "status": status,
                "metadata": json.dumps(metadata or {}),
            },
        )

    def get_by_code(self, cursor: Any, kb_code: str) -> dict[str, Any] | None:
        """Fetch a knowledge base by business code."""
        cursor.execute(
            """
            SELECT kid, kb_code, kb_name, kb_description, status, is_deleted, root_entry_id
            FROM knowledge_base
            WHERE kb_code = %(kb_code)s
              AND is_deleted = FALSE
            """,
            {"kb_code": kb_code},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def soft_delete_by_code(self, cursor: Any, *, kb_code: str) -> None:
        """Logically delete one knowledge base."""
        cursor.execute(
            """
            UPDATE knowledge_base
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE kb_code = %(kb_code)s
            """,
            {"kb_code": kb_code},
        )
