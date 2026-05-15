"""Repository for pure metadata search and metadata backfill."""

from __future__ import annotations

from typing import Any


def _extract_value(row: dict[str, Any]) -> Any:
    vt = row["value_type"]
    if vt == "string":
        return row["value_string"]
    elif vt == "number":
        return row["value_number"]
    elif vt == "boolean":
        return row["value_boolean"]
    elif vt == "datetime":
        dt = row["value_datetime"]
        return dt.isoformat() if dt else None
    elif vt == "stringList":
        return row["value_string_list"] or []
    return None


class MetadataSearchRepository:
    """SQL queries for metadata-filtered file search and metadata backfill."""

    async def search_files(
        self,
        cursor: Any,
        *,
        kb_ids: list[int],
        where_sql: str,
        where_params: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        conditions = [
            "fe.knowledge_base_id = ANY(%(kb_ids)s)",
            "fe.entry_type = 'FILE'",
            "fe.is_deleted = false",
        ]
        if where_sql:
            conditions.append(where_sql)

        full_where = " AND ".join(conditions)
        sql = f"""
            WITH RECURSIVE path_parts AS (
                SELECT fe.kid, fe.name AS segment, fe.parent_entry_id, 0 AS depth,
                       kb.kid AS kb_id, CAST(kb.kid AS text) AS kb_code
                FROM knowledge_fs_entry fe
                JOIN knowledge_base kb ON kb.kid = fe.knowledge_base_id
                WHERE {full_where}

                UNION ALL

                SELECT pp.kid, p.name, p.parent_entry_id, pp.depth + 1,
                       NULL::bigint, NULL::text
                FROM path_parts pp
                JOIN knowledge_fs_entry p ON p.kid = pp.parent_entry_id
                WHERE pp.parent_entry_id IS NOT NULL
            )
            SELECT kid,
                   MAX(kb_id) AS kb_id,
                   MAX(kb_code) AS kb_code,
                   string_agg(segment, '/' ORDER BY depth DESC) AS full_path
            FROM path_parts
            WHERE kb_id IS NOT NULL OR parent_entry_id IS NULL
            GROUP BY kid
            ORDER BY kid DESC
            LIMIT %(limit)s
        """
        params = {**where_params, "kb_ids": kb_ids, "limit": limit}
        await cursor.execute(sql, params)
        return await cursor.fetchall()

    async def backfill_metadata(
        self,
        cursor: Any,
        *,
        fs_entry_ids: list[int],
        property_names: list[str] | None,
    ) -> dict[int, dict[str, Any]]:
        if not fs_entry_ids:
            return {}

        name_filter = ""
        params: dict[str, Any] = {"entry_ids": fs_entry_ids}
        if property_names:
            name_filter = "AND p.property_name = ANY(%(prop_names)s)"
            params["prop_names"] = property_names

        sql = f"""
            SELECT v.fs_entry_id, p.property_name, p.value_type,
                   v.value_string, v.value_number, v.value_boolean,
                   v.value_datetime, v.value_string_list
            FROM knowledge_file_metadata_value v
            JOIN knowledge_metadata_property_def p ON p.kid = v.property_def_id
            WHERE v.fs_entry_id = ANY(%(entry_ids)s)
              AND v.is_deleted = false
              AND p.is_deleted = false
              {name_filter}
        """
        await cursor.execute(sql, params)
        rows = await cursor.fetchall()

        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            entry_id = row["fs_entry_id"]
            if entry_id not in result:
                result[entry_id] = {}
            result[entry_id][row["property_name"]] = {
                "valueType": row["value_type"],
                "value": _extract_value(row),
            }
        return result
