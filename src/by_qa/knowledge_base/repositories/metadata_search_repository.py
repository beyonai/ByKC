"""Repository for pure metadata search and metadata backfill."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from by_qa.knowledge_base.metadata_types import (
    SYSTEM_FIELD_VALUE_TYPES,
    extract_system_metadata,
)


def _extract_value(row: dict[str, Any]) -> Any:
    vt = row["value_type"]
    if vt == "string":
        return row["value_string"]
    elif vt == "number":
        raw = row["value_number"]
        if raw is None:
            return None
        if isinstance(raw, Decimal):
            if raw == raw.to_integral_value():
                return int(raw)
            return float(raw)
        return raw
    elif vt == "boolean":
        return row["value_boolean"]
    elif vt == "datetime":
        dt = row["value_datetime"]
        return dt.isoformat() if dt else None
    elif vt == "stringList":
        return row["value_string_list"] or []
    return None


class MetadataSearchRepository:
    """SQL queries for metadata-filtered entry search and metadata backfill."""

    async def search_entries(
        self,
        cursor: Any,
        *,
        kb_ids: list[int],
        where_sql: str,
        where_params: dict[str, Any],
        limit: int,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions = [
            "fe.knowledge_base_id = ANY(%(kb_ids)s)",
            "fe.is_deleted = false",
        ]
        if where_sql:
            conditions.append(where_sql)

        full_where = " AND ".join(conditions)
        sql = f"""
            SELECT fe.kid,
                   kb.kid AS kb_id,
                   CAST(kb.kid AS text) AS kb_code,
                   fe.entry_type,
                   ltrim(fe.virtual_path, '/') AS full_path
            FROM knowledge_fs_entry fe
            JOIN knowledge_base kb ON kb.kid = fe.knowledge_base_id
            WHERE {full_where}
            ORDER BY fe.updated_at ASC, fe.kid ASC
            LIMIT %(limit)s
            OFFSET %(offset)s
        """
        params = {
            **where_params,
            "kb_ids": kb_ids,
            "limit": limit,
            "offset": offset,
        }
        await cursor.execute(sql, params)
        return await cursor.fetchall()

    async def count_entries(
        self,
        cursor: Any,
        *,
        kb_ids: list[int],
        where_sql: str,
        where_params: dict[str, Any],
    ) -> int:
        """Count live matching files and directories for stable pagination."""
        conditions = [
            "fe.knowledge_base_id = ANY(%(kb_ids)s)",
            "fe.is_deleted = false",
        ]
        if where_sql:
            conditions.append(where_sql)
        await cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM knowledge_fs_entry fe
            WHERE {" AND ".join(conditions)}
            """,
            {**where_params, "kb_ids": kb_ids},
        )
        row = await cursor.fetchone()
        if not row:
            return 0
        return int(row["total"])

    async def backfill_metadata(
        self,
        cursor: Any,
        *,
        fs_entry_ids: list[int],
        property_names: list[str] | None,
    ) -> dict[int, dict[str, Any]]:
        if not fs_entry_ids:
            return {}

        requested_names = (
            set(property_names)
            if property_names is not None
            else set(SYSTEM_FIELD_VALUE_TYPES)
        )
        system_names = requested_names.intersection(SYSTEM_FIELD_VALUE_TYPES)
        result: dict[int, dict[str, Any]] = {}
        if system_names:
            await cursor.execute(
                """
                SELECT kid, name, file_size, mime_type, checksum, virtual_path,
                       created_at, updated_at
                FROM knowledge_fs_entry
                WHERE kid = ANY(%(entry_ids)s)
                  AND is_deleted = false
                """,
                {"entry_ids": fs_entry_ids},
            )
            for row in await cursor.fetchall():
                result[row["kid"]] = extract_system_metadata(row, list(system_names))

        custom_names = (
            None
            if property_names is None
            else [
                name for name in property_names if name not in SYSTEM_FIELD_VALUE_TYPES
            ]
        )
        if custom_names == []:
            return result

        name_filter = ""
        params: dict[str, Any] = {"entry_ids": fs_entry_ids}
        if custom_names:
            name_filter = "AND v.property_name = ANY(%(prop_names)s)"
            params["prop_names"] = custom_names

        sql = f"""
            SELECT v.fs_entry_id, v.property_name, v.value_type,
                   v.value_string, v.value_number, v.value_boolean,
                   v.value_datetime, v.value_string_list
            FROM knowledge_file_metadata_value v
            WHERE v.fs_entry_id = ANY(%(entry_ids)s)
              AND v.is_deleted = false
              {name_filter}
        """
        await cursor.execute(sql, params)
        rows = await cursor.fetchall()

        for row in rows:
            entry_id = row["fs_entry_id"]
            if entry_id not in result:
                result[entry_id] = {}
            result[entry_id][row["property_name"]] = {
                "valueType": row["value_type"],
                "value": _extract_value(row),
            }
        return result
