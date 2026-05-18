"""Repository for file-level metadata values."""

from __future__ import annotations

import json
from typing import Any

from by_qa.knowledge_base.metadata_types import VALUE_TYPE_TO_COLUMN


class FileMetadataValueRepository:
    """CRUD operations on knowledge_file_metadata_value."""

    def _value_column(self, value_type: str) -> str:
        return VALUE_TYPE_TO_COLUMN[value_type]

    async def upsert_value(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        knowledge_base_id: int,
        property_def_id: int,
        value_type: str,
        value: Any,
    ) -> dict[str, Any] | None:
        col = self._value_column(value_type)
        serialized = json.dumps(value) if value_type == "stringList" else value

        # Try UPDATE first (portable approach that works with OpenGauss)
        await cursor.execute(
            f"""
            UPDATE knowledge_file_metadata_value
            SET {col} = %(value)s,
                is_deleted = false,
                updated_at = NOW()
            WHERE fs_entry_id = %(fs_entry_id)s
              AND property_def_id = %(property_def_id)s
              AND is_deleted = false
            RETURNING kid
            """,
            {
                "fs_entry_id": fs_entry_id,
                "knowledge_base_id": knowledge_base_id,
                "property_def_id": property_def_id,
                "value": serialized,
            },
        )
        row = await cursor.fetchone()
        if row:
            return row

        # No existing row — INSERT new one
        await cursor.execute(
            f"""
            INSERT INTO knowledge_file_metadata_value (
                fs_entry_id, knowledge_base_id, property_def_id,
                {col}, is_deleted, created_at, updated_at
            )
            VALUES (
                %(fs_entry_id)s, %(knowledge_base_id)s, %(property_def_id)s,
                %(value)s, false, NOW(), NOW()
            )
            RETURNING kid
            """,
            {
                "fs_entry_id": fs_entry_id,
                "knowledge_base_id": knowledge_base_id,
                "property_def_id": property_def_id,
                "value": serialized,
            },
        )
        return await cursor.fetchone()

    async def soft_delete_value(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_def_id: int,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            UPDATE knowledge_file_metadata_value
            SET is_deleted = true, updated_at = NOW()
            WHERE fs_entry_id = %(fs_entry_id)s
              AND property_def_id = %(property_def_id)s
              AND is_deleted = false
            RETURNING kid
            """,
            {
                "fs_entry_id": fs_entry_id,
                "property_def_id": property_def_id,
            },
        )
        return await cursor.fetchone()

    async def get_active_value(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_def_id: int,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT v.kid, v.value_string, v.value_number, v.value_boolean,
                   v.value_datetime, v.value_string_list,
                   p.property_name, p.value_type
            FROM knowledge_file_metadata_value v
            JOIN knowledge_metadata_property_def p ON p.kid = v.property_def_id
            WHERE v.fs_entry_id = %(fs_entry_id)s
              AND v.property_def_id = %(property_def_id)s
              AND v.is_deleted = false
            """,
            {
                "fs_entry_id": fs_entry_id,
                "property_def_id": property_def_id,
            },
        )
        return await cursor.fetchone()

    async def get_file_metadata(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if property_names:
            await cursor.execute(
                """
                SELECT p.property_name, p.value_type,
                       v.value_string, v.value_number, v.value_boolean,
                       v.value_datetime, v.value_string_list
                FROM knowledge_file_metadata_value v
                JOIN knowledge_metadata_property_def p ON p.kid = v.property_def_id
                WHERE v.fs_entry_id = %(fs_entry_id)s
                  AND v.is_deleted = false
                  AND p.property_name = ANY(%(names)s)
                ORDER BY p.kid
                """,
                {"fs_entry_id": fs_entry_id, "names": property_names},
            )
        else:
            await cursor.execute(
                """
                SELECT p.property_name, p.value_type,
                       v.value_string, v.value_number, v.value_boolean,
                       v.value_datetime, v.value_string_list
                FROM knowledge_file_metadata_value v
                JOIN knowledge_metadata_property_def p ON p.kid = v.property_def_id
                WHERE v.fs_entry_id = %(fs_entry_id)s
                  AND v.is_deleted = false
                ORDER BY p.kid
                """,
                {"fs_entry_id": fs_entry_id},
            )
        return await cursor.fetchall()

    async def list_used_properties(
        self,
        cursor: Any,
        *,
        knowledge_base_ids: list[int],
    ) -> list[dict[str, Any]]:
        await cursor.execute(
            """
            SELECT DISTINCT p.property_name, p.value_type, p.description
            FROM knowledge_file_metadata_value v
            JOIN knowledge_metadata_property_def p ON p.kid = v.property_def_id
            WHERE v.knowledge_base_id = ANY(%(kb_ids)s)
              AND v.is_deleted = false
              AND p.is_deleted = false
            ORDER BY p.property_name
            """,
            {"kb_ids": knowledge_base_ids},
        )
        return await cursor.fetchall()
