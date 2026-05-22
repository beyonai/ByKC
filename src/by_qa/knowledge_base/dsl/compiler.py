"""Compile Agent DSL where clause into SQL WHERE fragment + params."""

from __future__ import annotations

import json
from typing import Any

from by_qa.knowledge_base.metadata_types import (
    SYSTEM_FIELD_TO_FE_EXPR,
    VALUE_TYPE_TO_COLUMN,
)

COMPARISON_OPS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}

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
        def_id_key = ctx.next_param(prop["def_id"])
        col = _value_column(prop["value_type"])
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE mv.fs_entry_id = fe.kid "
            f"AND mv.property_def_id = %({def_id_key})s "
            f"AND mv.is_deleted = false "
            f"AND mv.{col} IS NOT NULL)"
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
        def_id_key = ctx.next_param(prop["def_id"])
        val_key = ctx.next_param(escaped + "%")
        col = _value_column(prop["value_type"])
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE mv.fs_entry_id = fe.kid "
            f"AND mv.property_def_id = %({def_id_key})s "
            f"AND mv.is_deleted = false "
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
        def_id_key = ctx.next_param(prop["def_id"])
        val_key = ctx.next_param(like_value)
        col = _value_column(prop["value_type"])
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE mv.fs_entry_id = fe.kid "
            f"AND mv.property_def_id = %({def_id_key})s "
            f"AND mv.is_deleted = false "
            f"AND mv.{col} LIKE %({val_key})s ESCAPE '{LIKE_ESCAPE_CHAR}')"
        )

    if operator == "contains":
        # `contains` is validator-restricted to user-defined stringList
        # fields; no system-field branch here.
        body = node["contains"]
        field_name = body["fieldName"]
        value = body["value"]
        prop = property_map[field_name]
        def_id_key = ctx.next_param(prop["def_id"])
        val_key = ctx.next_param(json.dumps([value]))
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE mv.fs_entry_id = fe.kid "
            f"AND mv.property_def_id = %({def_id_key})s "
            f"AND mv.is_deleted = false "
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
        def_id_key = ctx.next_param(prop["def_id"])
        val_key = ctx.next_param(value_list)
        col = _value_column(prop["value_type"])
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE mv.fs_entry_id = fe.kid "
            f"AND mv.property_def_id = %({def_id_key})s "
            f"AND mv.is_deleted = false "
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
        def_id_key = ctx.next_param(prop["def_id"])
        val_key = ctx.next_param(value)
        col = _value_column(prop["value_type"])
        return (
            f"EXISTS (SELECT 1 FROM knowledge_file_metadata_value mv "
            f"WHERE mv.fs_entry_id = fe.kid "
            f"AND mv.property_def_id = %({def_id_key})s "
            f"AND mv.is_deleted = false "
            f"AND mv.{col} {sql_op} %({val_key})s)"
        )

    return "TRUE"
