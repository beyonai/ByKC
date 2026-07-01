"""Unit tests for metadata DSL SQL compilation."""

from __future__ import annotations

from datetime import datetime

from by_qa.knowledge_base.dsl.compiler import compile_where_to_sql


def test_custom_field_sql_matches_property_name_and_value_type_not_definition_id():
    where = {"eq": {"fieldName": "status", "value": "active"}}

    sql, params = compile_where_to_sql(
        where,
        property_map={"status": {"value_type": "string"}},
    )

    normalized = " ".join(sql.lower().split())
    assert "mv.property_name = " in normalized
    assert "mv.value_type = " in normalized
    assert "property_def_id" not in normalized
    assert "mv.value_string = " in normalized
    assert params["dsl_p1"] == "status"
    assert params["dsl_p2"] == "string"
    assert params["dsl_p3"] == "active"


def test_same_property_name_can_compile_for_different_value_types():
    number_sql, number_params = compile_where_to_sql(
        {"gte": {"fieldName": "priority", "value": 3}},
        property_map={"priority": {"value_type": "number"}},
    )
    string_sql, string_params = compile_where_to_sql(
        {"prefix": {"fieldName": "priority", "value": "P"}},
        property_map={"priority": {"value_type": "string"}},
    )

    assert "mv.value_number" in number_sql
    assert "mv.value_string" in string_sql
    assert number_params["dsl_p1"] == string_params["dsl_p1"] == "priority"
    assert number_params["dsl_p2"] == "number"
    assert string_params["dsl_p2"] == "string"


def test_order_filter_on_iso_date_string_targets_datetime_metadata():
    sql, params = compile_where_to_sql(
        {
            "and": [
                {"prefix": {"fieldName": "会议主题", "value": "DataCloud"}},
                {"gt": {"fieldName": "会议日期", "value": "2025-01-01"}},
            ]
        },
        property_map={"会议主题": {}, "会议日期": {}},
    )

    assert "mv.value_string LIKE" in sql
    assert "mv.value_datetime >" in sql
    assert "mv.value_string >" in sql
    assert datetime(2025, 1, 1) in params.values()
    assert "2025-01-01" in params.values()
