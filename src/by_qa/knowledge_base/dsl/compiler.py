"""Compile Agent DSL where clause into SQL WHERE fragment + params."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from by_qa.knowledge_base.metadata_types import (
    SYSTEM_FIELD_TO_FE_EXPR,
    VALUE_TYPE_TO_COLUMN,
    infer_metadata_value_type,
)

COMPARISON_OPS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}
ORDER_OPS = {"gt", "gte", "lt", "lte"}

LIKE_ESCAPE_CHAR = "!"


def compile_where_to_sql(
    where: dict[str, Any] | None,
    *,
    property_map: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    if not where:
        return "", {}
    ctx = _CompilerContext()
    sql = _compile_node(where, property_map, ctx)
    return sql, ctx.params


class _CompilerContext:
    def __init__(self):
        self.params: dict[str, Any] = {}
        self._counter = 0

    def next_param(self, value: Any) -> str:
        self._counter += 1
        key = f"dsl_p{self._counter}"
        self.params[key] = value
        return key


def _value_column(value_type: str) -> str:
    return VALUE_TYPE_TO_COLUMN[value_type]


def _custom_field_filter(
    ctx: _CompilerContext,
    *,
    field_name: str,
    value_type: str | None = None,
) -> str:
    field_key = ctx.next_param(field_name)
    parts = [
        "mv.fs_entry_id = fe.kid",
        f"mv.property_name = %({field_key})s",
        "mv.is_deleted = false",
    ]
    if value_type is not None:
        type_key = ctx.next_param(value_type)
        parts.append(f"mv.value_type = %({type_key})s")
    return " AND ".join(parts)


def _query_value_type(operator: str, value: Any) -> str:
    if operator in ("prefix", "wildcard"):
        return "string"
    if operator == "contains":
        return "stringList"
    if operator in ORDER_OPS and isinstance(value, str):
        if _parse_datetime_literal(value) is not None:
            return "datetime"
    return infer_metadata_value_type(value)


def _parse_datetime_literal(value: str) -> datetime | None:
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_query_value(value: Any, value_type: str) -> Any:
    if value_type == "datetime" and isinstance(value, str):
        parsed = _parse_datetime_literal(value)
        if parsed is not None:
            return parsed
    return value


def _escape_like_literal(value: str) -> str:
    """Escape SQL LIKE metacharacters using a single-character ESCAPE token."""
    return (
        value.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
        .replace("%", LIKE_ESCAPE_CHAR + "%")
        .replace("_", LIKE_ESCAPE_CHAR + "_")
    )


def _compile_node(
    node: dict[str, Any],
    property_map: dict[str, dict[str, Any]],
    ctx: _CompilerContext,
) -> str:
    operator = next(iter(node))

    if operator == "and":
        parts = [_compile_node(child, property_map, ctx) for child in node["and"]]
        return "(" + " AND ".join(parts) + ")"

    if operator == "or":
        parts = [_compile_node(child, property_map, ctx) for child in node["or"]]
        return "(" + " OR ".join(parts) + ")"

    if operator == "not":
        inner = _compile_node(node["not"], property_map, ctx)
        return f"NOT ({inner})"

    if operator == "exists":
        body = node["exists"]
        field_name = body["fieldName"]
        if field_name in SYSTEM_FIELD_TO_FE_EXPR:
            expr, _ = SYSTEM_FIELD_TO_FE_EXPR[field_name]
            return f"({expr} IS NOT NULL)"
        prop = property_map[field_name]
        value_type = prop.get("value_type")
        value_columns = " OR ".join(
            f"mv.{col} IS NOT NULL" for col in VALUE_TYPE_TO_COLUMN.values()
        )
        filters = _custom_field_filter(
            ctx, field_name=field_name, value_type=value_type
        )
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE {filters} "
            f"AND ({value_columns}))"
        )

    if operator == "prefix":
        body = node["prefix"]
        field_name = body["fieldName"]
        value = body["value"]
        escaped = _escape_like_literal(value)
        if field_name in SYSTEM_FIELD_TO_FE_EXPR:
            expr, _ = SYSTEM_FIELD_TO_FE_EXPR[field_name]
            val_key = ctx.next_param(escaped + "%")
            return f"({expr} LIKE %({val_key})s ESCAPE '{LIKE_ESCAPE_CHAR}')"
        prop = property_map[field_name]
        value_type = prop.get("value_type") or _query_value_type(operator, value)
        col = _value_column(value_type)
        filters = _custom_field_filter(
            ctx, field_name=field_name, value_type=value_type
        )
        val_key = ctx.next_param(escaped + "%")
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE {filters} "
            f"AND mv.{col} LIKE %({val_key})s ESCAPE '{LIKE_ESCAPE_CHAR}')"
        )

    if operator == "wildcard":
        body = node["wildcard"]
        field_name = body["fieldName"]
        value = body["value"]
        # Translate ES wildcard to SQL LIKE:
        # * -> % (zero or more chars)
        # ? -> _ (exactly one char)
        # Escape SQL LIKE metacharacters first, then replace ES wildcards.
        like_value = _escape_like_literal(value)
        like_value = like_value.replace("*", "%").replace("?", "_")
        if field_name in SYSTEM_FIELD_TO_FE_EXPR:
            expr, _ = SYSTEM_FIELD_TO_FE_EXPR[field_name]
            val_key = ctx.next_param(like_value)
            return f"({expr} LIKE %({val_key})s ESCAPE '{LIKE_ESCAPE_CHAR}')"
        prop = property_map[field_name]
        value_type = prop.get("value_type") or _query_value_type(operator, value)
        col = _value_column(value_type)
        filters = _custom_field_filter(
            ctx, field_name=field_name, value_type=value_type
        )
        val_key = ctx.next_param(like_value)
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE {filters} "
            f"AND mv.{col} LIKE %({val_key})s ESCAPE '{LIKE_ESCAPE_CHAR}')"
        )

    if operator == "contains":
        # `contains` is validator-restricted to user-defined stringList
        # fields; no system-field branch here.
        body = node["contains"]
        field_name = body["fieldName"]
        value = body["value"]
        prop = property_map[field_name]
        value_type = prop.get("value_type") or _query_value_type(operator, value)
        filters = _custom_field_filter(
            ctx, field_name=field_name, value_type=value_type
        )
        val_key = ctx.next_param(json.dumps([value]))
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE {filters} "
            f"AND mv.value_string_list @> %({val_key})s::jsonb)"
        )

    if operator == "in":
        body = node["in"]
        field_name = body["fieldName"]
        value_list = body["value"]
        if field_name in SYSTEM_FIELD_TO_FE_EXPR:
            expr, _ = SYSTEM_FIELD_TO_FE_EXPR[field_name]
            val_key = ctx.next_param(value_list)
            return f"({expr} = ANY(%({val_key})s))"
        prop = property_map[field_name]
        value_type = prop.get("value_type") or _query_value_type(
            operator, value_list[0]
        )
        col = _value_column(value_type)
        filters = _custom_field_filter(
            ctx, field_name=field_name, value_type=value_type
        )
        val_key = ctx.next_param(value_list)
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE {filters} "
            f"AND mv.{col} = ANY(%({val_key})s))"
        )

    if operator in COMPARISON_OPS:
        body = node[operator]
        field_name = body["fieldName"]
        value = body["value"]
        sql_op = COMPARISON_OPS[operator]
        if field_name in SYSTEM_FIELD_TO_FE_EXPR:
            expr, _ = SYSTEM_FIELD_TO_FE_EXPR[field_name]
            val_key = ctx.next_param(value)
            return f"({expr} {sql_op} %({val_key})s)"
        prop = property_map[field_name]
        configured_value_type = prop.get("value_type")
        if (
            configured_value_type is None
            and operator in ORDER_OPS
            and isinstance(value, str)
            and (parsed_datetime := _parse_datetime_literal(value)) is not None
        ):
            datetime_filters = _custom_field_filter(
                ctx,
                field_name=field_name,
                value_type="datetime",
            )
            datetime_key = ctx.next_param(parsed_datetime)
            string_filters = _custom_field_filter(
                ctx,
                field_name=field_name,
                value_type="string",
            )
            string_key = ctx.next_param(value)
            return (
                "(EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
                f"WHERE {datetime_filters} "
                f"AND mv.value_datetime {sql_op} %({datetime_key})s) "
                "OR EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
                f"WHERE {string_filters} "
                f"AND mv.value_string {sql_op} %({string_key})s))"
            )
        value_type = configured_value_type or _query_value_type(operator, value)
        col = _value_column(value_type)
        filters = _custom_field_filter(
            ctx, field_name=field_name, value_type=value_type
        )
        val_key = ctx.next_param(_normalize_query_value(value, value_type))
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE {filters} "
            f"AND mv.{col} {sql_op} %({val_key})s)"
        )

    return "TRUE"
