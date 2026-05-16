"""Repository for global metadata property definitions."""

from __future__ import annotations

import json
from typing import Any

from by_qa.knowledge_base.metadata_types import SYSTEM_FIELD_VALUE_TYPES

# Names reserved by the file system entry / knowledge base main tables.
# Users cannot create custom metadata properties under these names.
# filePath is reserved here even though the DSL does not expose it as a
# queryable system field — the underlying column is ltree and lacks a
# stable string equality semantic for AST comparisons.
SYSTEM_FIELD_NAMES: frozenset[str] = frozenset(
    set(SYSTEM_FIELD_VALUE_TYPES) | {"filePath"}
)


class MetadataPropertyRepository:
    """CRUD operations on knowledge_metadata_property_def."""

    async def create(
        self,
        cursor: Any,
        *,
        property_name: str,
        value_type: str,
        description: str | None,
        ext_params: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            INSERT INTO knowledge_metadata_property_def (
                property_name, value_type, description, ext_params,
                created_at, updated_at
            )
            VALUES (
                %(property_name)s, %(value_type)s, %(description)s,
                %(ext_params)s, NOW(), NOW()
            )
            RETURNING kid, property_name, value_type, description, ext_params
            """,
            {
                "property_name": property_name,
                "value_type": value_type,
                "description": description,
                "ext_params": json.dumps(ext_params) if ext_params else None,
            },
        )
        return await cursor.fetchone()

    async def get_by_name(
        self, cursor: Any, property_name: str
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT kid, property_name, value_type, description, ext_params
            FROM knowledge_metadata_property_def
            WHERE property_name = %(property_name)s
              AND is_deleted = false
            """,
            {"property_name": property_name},
        )
        return await cursor.fetchone()

    async def list_properties(
        self, cursor: Any, *, property_names: list[str] | None
    ) -> list[dict[str, Any]]:
        if property_names:
            await cursor.execute(
                """
                SELECT kid, property_name, value_type, description, ext_params
                FROM knowledge_metadata_property_def
                WHERE property_name = ANY(%(names)s)
                  AND is_deleted = false
                ORDER BY kid
                """,
                {"names": property_names},
            )
        else:
            await cursor.execute(
                """
                SELECT kid, property_name, value_type, description, ext_params
                FROM knowledge_metadata_property_def
                WHERE is_deleted = false
                ORDER BY kid
                """,
                None,
            )
        return await cursor.fetchall()

    async def soft_delete(
        self, cursor: Any, *, property_name: str
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            UPDATE knowledge_metadata_property_def
            SET is_deleted = true, updated_at = NOW()
            WHERE property_name = %(property_name)s
              AND is_deleted = false
            RETURNING kid
            """,
            {"property_name": property_name},
        )
        return await cursor.fetchone()

    async def count_references(self, cursor: Any, *, property_def_id: int) -> int:
        await cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM knowledge_file_metadata_value
            WHERE property_def_id = %(property_def_id)s
              AND is_deleted = false
            """,
            {"property_def_id": property_def_id},
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0
