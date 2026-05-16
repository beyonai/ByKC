"""Persistence helpers for knowledge_base rows."""

from typing import Any


class KnowledgeBaseRepository:
    """Repository for knowledge base metadata."""

    async def get_by_name(self, cursor: Any, kb_name: str) -> dict[str, Any] | None:
        """Fetch an active knowledge base by name."""
        await cursor.execute(
            """
            SELECT kid, kb_name, kb_description, is_deleted
            FROM knowledge_base
            WHERE kb_name = %(kb_name)s
              AND is_deleted = FALSE
            """,
            {"kb_name": kb_name},
        )
        return await cursor.fetchone()

    async def create_knowledge_base(
        self,
        cursor: Any,
        *,
        kb_name: str,
        kb_description: str | None,
    ) -> dict[str, Any] | None:
        """Insert a knowledge base."""
        await cursor.execute(
            """
            INSERT INTO knowledge_base (
                kb_name,
                kb_description,
                created_at,
                updated_at
            )
            VALUES (
                %(kb_name)s,
                %(kb_description)s,
                NOW(),
                NOW()
            )
            RETURNING kid, kb_name, kb_description, is_deleted
            """,
            {
                "kb_name": kb_name,
                "kb_description": kb_description,
            },
        )
        return await cursor.fetchone()

    async def get_by_code(self, cursor: Any, kb_code: str) -> dict[str, Any] | None:
        """Fetch a knowledge base by business code.

        A non-integer kb_code can never match the bigint primary key, so we
        treat it as "not found" rather than letting psycopg raise an
        InvalidTextRepresentation that callers cannot map to the documented
        "knowledge base not found" envelope.
        """
        try:
            int(kb_code)
        except (TypeError, ValueError):
            return None
        await cursor.execute(
            """
            SELECT kid, kb_name, kb_description, is_deleted
            FROM knowledge_base
            WHERE kid = %(kb_code)s::bigint
              AND is_deleted = FALSE
            """,
            {"kb_code": kb_code},
        )
        return await cursor.fetchone()

    async def soft_delete_by_code(self, cursor: Any, *, kb_code: str) -> None:
        """Logically delete one knowledge base."""
        try:
            int(kb_code)
        except (TypeError, ValueError):
            return
        await cursor.execute(
            """
            UPDATE knowledge_base
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE kid = %(kb_code)s::bigint
            """,
            {"kb_code": kb_code},
        )

    async def update_knowledge_base(
        self,
        cursor: Any,
        *,
        kb_code: str,
        updates: dict[str, Any],
    ) -> None:
        """Update mutable business fields of one knowledge base."""
        assignments: list[str] = []
        params: dict[str, Any] = {"kb_code": kb_code}

        if "kb_name" in updates:
            assignments.append("kb_name = %(kb_name)s")
            params["kb_name"] = updates["kb_name"]
        if "kb_description" in updates:
            assignments.append("kb_description = %(kb_description)s")
            params["kb_description"] = updates["kb_description"]

        if not assignments:
            return

        try:
            int(kb_code)
        except (TypeError, ValueError):
            return

        await cursor.execute(
            f"""
            UPDATE knowledge_base
            SET {", ".join(assignments)},
                updated_at = NOW()
            WHERE kid = %(kb_code)s::bigint
              AND is_deleted = FALSE
            """,
            params,
        )
