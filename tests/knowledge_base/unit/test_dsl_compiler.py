"""Unit tests for Agent DSL compiler (where AST -> SQL)."""

from __future__ import annotations

from by_qa.knowledge_base.dsl.compiler import compile_where_to_sql

PROPERTY_MAP = {
    "status": {"def_id": 1, "value_type": "string"},
    "tags": {"def_id": 2, "value_type": "stringList"},
    "priority": {"def_id": 3, "value_type": "number"},
    "archived": {"def_id": 4, "value_type": "boolean"},
}


def test_none_where_returns_empty():
    sql, params = compile_where_to_sql(None, property_map=PROPERTY_MAP)
    assert sql == ""
    assert params == {}


def test_simple_eq_string():
    where = {"eq": {"fieldName": "status", "value": "active"}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "property_def_id" in sql
    assert "value_string" in sql
    assert "active" in params.values()


def test_simple_eq_number():
    where = {"eq": {"fieldName": "priority", "value": 3}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "value_number" in sql
    assert 3 in params.values()


def test_contains_string_list():
    where = {"contains": {"fieldName": "tags", "value": "contract"}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "value_string_list" in sql
    assert "contract" in str(params.values())


def test_and_combination():
    where = {
        "and": [
            {"eq": {"fieldName": "status", "value": "active"}},
            {"eq": {"fieldName": "priority", "value": 1}},
        ]
    }
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert " AND " in sql
    assert len(params) == 4  # 2 def_ids + 2 values


def test_or_combination():
    where = {
        "or": [
            {"eq": {"fieldName": "status", "value": "active"}},
            {"eq": {"fieldName": "status", "value": "draft"}},
        ]
    }
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert " OR " in sql


def test_not_operator():
    where = {"not": {"eq": {"fieldName": "status", "value": "draft"}}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "NOT" in sql


def test_exists_operator():
    where = {"exists": {"fieldName": "status"}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "EXISTS" in sql or "IS NOT NULL" in sql


def test_gt_number():
    where = {"gt": {"fieldName": "priority", "value": 2}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert ">" in sql
    assert 2 in params.values()


def test_in_operator():
    where = {"in": {"fieldName": "status", "value": ["active", "draft"]}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "ANY" in sql or "IN" in sql


def test_system_field_eq_emits_fe_column_not_exists():
    """System fields live on knowledge_fs_entry; compiler must reference fe.col."""
    where = {"eq": {"fieldName": "fileName", "value": "report.md"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.name" in sql
    assert "EXISTS" not in sql
    assert "report.md" in params.values()


def test_system_field_file_type_extracts_extension():
    """fileType is derived from the trailing extension of fe.name, lowercased."""
    where = {"eq": {"fieldName": "fileType", "value": "pdf"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.name" in sql
    assert "lower" in sql
    assert "pdf" in params.values()


def test_system_field_in_uses_fe_column_directly():
    where = {"in": {"fieldName": "fileType", "value": ["md", "pdf"]}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "= ANY(" in sql
    assert "EXISTS" not in sql


def test_system_field_gt_on_datetime_emits_direct_compare():
    where = {"gt": {"fieldName": "createdAt", "value": "2026-01-01T00:00:00Z"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.created_at" in sql
    assert "EXISTS" not in sql


def test_system_field_exists_uses_fe_column_directly():
    where = {"exists": {"fieldName": "mimeType"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.mime_type" in sql
    assert "IS NOT NULL" in sql
    assert "EXISTS" not in sql


def test_mixed_system_and_custom_fields_combine():
    """A real query may combine fe.* and metadata-table predicates."""
    where = {
        "and": [
            {"eq": {"fieldName": "fileType", "value": "md"}},
            {"eq": {"fieldName": "status", "value": "active"}},
        ]
    }
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert " AND " in sql
    assert "fe.name" in sql
    assert "value_string" in sql


def test_prefix_on_custom_string_field():
    where = {"prefix": {"fieldName": "status", "value": "act"}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "value_string" in sql
    assert "LIKE" in sql.upper()
    assert "act" in str(params.values())


def test_prefix_on_system_field_uses_fe_column():
    where = {"prefix": {"fieldName": "fileName", "value": "report"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.name" in sql
    assert "LIKE" in sql.upper()
    assert "EXISTS" not in sql
    assert "report%" in params.values()


def test_wildcard_on_custom_string_field():
    where = {"wildcard": {"fieldName": "status", "value": "act*ve"}}
    sql, params = compile_where_to_sql(where, property_map=PROPERTY_MAP)
    assert "value_string" in sql
    assert "LIKE" in sql.upper()
    assert "act%ve" in str(params.values())


def test_wildcard_on_system_field_with_question_mark():
    where = {"wildcard": {"fieldName": "fileName", "value": "report_?.md"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.name" in sql
    assert "LIKE" in sql.upper()
    assert "EXISTS" not in sql
    # The _ is escaped to \_, then ? is replaced with _
    param_value = next(iter(params.values()))
    assert "?" not in param_value, "? should be replaced by _"
    assert param_value.count("_") >= 2  # original _ (escaped \_) + ? -> _


def test_wildcard_star_only():
    where = {"wildcard": {"fieldName": "fileName", "value": "*"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.name" in sql
    assert "LIKE" in sql.upper()
    assert "%" in str(params.values())


def test_system_field_file_path_eq():
    where = {"eq": {"fieldName": "filePath", "value": "/docs/report.md"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.virtual_path" in sql
    assert "=" in sql
    assert "EXISTS" not in sql
    assert "/docs/report.md" in params.values()


def test_system_field_file_path_prefix():
    where = {"prefix": {"fieldName": "filePath", "value": "/docs/"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.virtual_path" in sql
    assert "LIKE" in sql.upper()
    assert "EXISTS" not in sql
    assert "/docs/%" in params.values()


def test_system_field_file_path_wildcard():
    where = {"wildcard": {"fieldName": "filePath", "value": "/docs/F?.*"}}
    sql, params = compile_where_to_sql(where, property_map={})
    assert "fe.virtual_path" in sql
    assert "LIKE" in sql.upper()
    assert "ESCAPE" in sql
    assert "EXISTS" not in sql
    param_values = list(params.values())
    assert any("/docs/F_" in str(v) for v in param_values)


def test_prefix_uses_single_character_escape_sequence():
    where = {"prefix": {"fieldName": "filePath", "value": "/docs/_100%!tmp"}}
    sql, params = compile_where_to_sql(where, property_map={})

    assert "ESCAPE '!'" in sql
    assert next(iter(params.values())) == "/docs/!_100!%!!tmp%"


def test_wildcard_uses_single_character_escape_sequence():
    where = {"wildcard": {"fieldName": "fileName", "value": "report_*!?.md"}}
    sql, params = compile_where_to_sql(where, property_map={})

    assert "ESCAPE '!'" in sql
    assert next(iter(params.values())) == "report!_%!!_.md"
