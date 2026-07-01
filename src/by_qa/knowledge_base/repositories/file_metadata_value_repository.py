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
        property_name: str,
        value_type: str,
        value: Any,
    ) -> dict[str, Any] | None:
        col = self._value_column(value_type)
        serialized = (
            json.dumps(value)
            if value_type == "stringList" and value is not None
            else value
        )
        clear_assignments = [
            f"{candidate_col} = NULL"
            for candidate_col in VALUE_TYPE_TO_COLUMN.values()
            if candidate_col != col
        ]
        clear_sql = ",\n                ".join(clear_assignments)

        # Try UPDATE first (portable approach that works with OpenGauss)
        await cursor.execute(
            f"""
            UPDATE knowledge_file_metadata_value
            SET {clear_sql},
                {col} = %(value)s,
                is_deleted = false,
                updated_at = NOW()
            WHERE fs_entry_id = %(fs_entry_id)s
              AND property_name = %(property_name)s
              AND value_type = %(value_type)s
              AND is_deleted = false
            RETURNING kid
            """,
            {
                "fs_entry_id": fs_entry_id,
                "knowledge_base_id": knowledge_base_id,
                "property_name": property_name,
                "value_type": value_type,
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
                fs_entry_id, knowledge_base_id, property_name, value_type,
                {col}, is_deleted, created_at, updated_at
            )
            VALUES (
                %(fs_entry_id)s, %(knowledge_base_id)s, %(property_name)s,
                %(value_type)s,
                %(value)s, false, NOW(), NOW()
            )
            RETURNING kid
            """,
            {
                "fs_entry_id": fs_entry_id,
                "knowledge_base_id": knowledge_base_id,
                "property_name": property_name,
                "value_type": value_type,
                "value": serialized,
            },
        )
        return await cursor.fetchone()

    async def soft_delete_value(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_name: str,
        value_type: str | None = None,
    ) -> dict[str, Any] | None:
        type_filter = ""
        params: dict[str, Any] = {
            "fs_entry_id": fs_entry_id,
            "property_name": property_name,
        }
        if value_type is not None:
            type_filter = "AND value_type = %(value_type)s"
            params["value_type"] = value_type
        await cursor.execute(
            f"""
            UPDATE knowledge_file_metadata_value
            SET is_deleted = true, updated_at = NOW()
            WHERE fs_entry_id = %(fs_entry_id)s
              AND property_name = %(property_name)s
              {type_filter}
              AND is_deleted = false
            RETURNING kid
            """,
            params,
        )
        return await cursor.fetchone()

    async def get_active_value(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        property_name: str,
        value_type: str,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT v.kid, v.value_string, v.value_number, v.value_boolean,
                   v.value_datetime, v.value_string_list,
                   v.property_name, v.value_type
            FROM knowledge_file_metadata_value v
            WHERE v.fs_entry_id = %(fs_entry_id)s
              AND v.property_name = %(property_name)s
              AND v.value_type = %(value_type)s
              AND v.is_deleted = false
            """,
            {
                "fs_entry_id": fs_entry_id,
                "property_name": property_name,
                "value_type": value_type,
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
                SELECT v.property_name, v.value_type,
                       v.value_string, v.value_number, v.value_boolean,
                       v.value_datetime, v.value_string_list
                FROM knowledge_file_metadata_value v
                WHERE v.fs_entry_id = %(fs_entry_id)s
                  AND v.is_deleted = false
                  AND v.property_name = ANY(%(names)s)
                ORDER BY v.kid
                """,
                {"fs_entry_id": fs_entry_id, "names": property_names},
            )
        else:
            await cursor.execute(
                """
                SELECT v.property_name, v.value_type,
                       v.value_string, v.value_number, v.value_boolean,
                       v.value_datetime, v.value_string_list
                FROM knowledge_file_metadata_value v
                WHERE v.fs_entry_id = %(fs_entry_id)s
                  AND v.is_deleted = false
                ORDER BY v.kid
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
            SELECT DISTINCT v.property_name, v.value_type, NULL::text AS description
            FROM knowledge_file_metadata_value v
            WHERE v.knowledge_base_id = ANY(%(kb_ids)s)
              AND v.is_deleted = false
            ORDER BY v.property_name, v.value_type
            """,
            {"kb_ids": knowledge_base_ids},
        )
        return await cursor.fetchall()
