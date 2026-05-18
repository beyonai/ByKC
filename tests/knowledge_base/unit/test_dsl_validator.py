"""Unit tests for Agent DSL validator."""

from __future__ import annotations

import pytest

from by_qa.knowledge_base.dsl.errors import DslValidationError
from by_qa.knowledge_base.dsl.validator import validate_where_clause

KNOWN_FIELDS = {
    "status": "string",
    "tags": "stringList",
    "priority": "number",
    "archived": "boolean",
    "effectiveAt": "datetime",
}


def test_valid_simple_eq():
    where = {"eq": {"fieldName": "status", "value": "active"}}
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_valid_and_combination():
    where = {
        "and": [
            {"eq": {"fieldName": "status", "value": "active"}},
            {"contains": {"fieldName": "tags", "value": "contract"}},
        ]
    }
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_unknown_field_raises():
    where = {"eq": {"fieldName": "nonexistent", "value": "x"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "UNKNOWN_FIELD"


def test_too_deep_nesting_raises():
    where = {
        "and": [
            {
                "and": [
                    {"and": [{"and": [{"eq": {"fieldName": "status", "value": "x"}}]}]}
                ]
            }
        ]
    }
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "TOO_DEEP_BOOLEAN_NESTING"


def test_too_many_conditions_raises():
    leaves = [{"eq": {"fieldName": "status", "value": f"v{i}"}} for i in range(13)]
    where = {"and": leaves}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "TOO_MANY_CONDITIONS"


def test_invalid_boolean_node_raises():
    where = {"and": "not_an_array"}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_BOOLEAN_NODE"


def test_unsupported_operator_raises():
    where = {"regex": {"fieldName": "status", "value": ".*"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "UNSUPPORTED_OPERATOR"


def test_none_where_is_valid():
    validate_where_clause(None, known_fields=KNOWN_FIELDS)


def test_exists_operator():
    where = {"exists": {"fieldName": "status"}}
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_not_operator():
    where = {"not": {"eq": {"fieldName": "status", "value": "draft"}}}
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_eq_string_rejects_non_string_value():
    where = {"eq": {"fieldName": "status", "value": 123}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"
    assert exc_info.value.error_list[0].path == "where.eq.value"


def test_eq_number_rejects_string_value():
    where = {"eq": {"fieldName": "priority", "value": "3"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_eq_number_rejects_boolean_value():
    """bool is an int subclass in Python; the validator must reject it."""
    where = {"eq": {"fieldName": "priority", "value": True}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_eq_boolean_accepts_bool():
    where = {"eq": {"fieldName": "archived", "value": True}}
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_eq_datetime_accepts_iso8601_with_z():
    where = {"eq": {"fieldName": "effectiveAt", "value": "2026-05-15T10:00:00Z"}}
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_eq_datetime_rejects_non_iso_string():
    where = {"eq": {"fieldName": "effectiveAt", "value": "yesterday"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_in_requires_non_empty_array():
    where = {"in": {"fieldName": "status", "value": []}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_in_rejects_element_type_mismatch():
    where = {"in": {"fieldName": "status", "value": ["active", 1]}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"
    assert exc_info.value.error_list[0].path == "where.in.value[1]"


def test_in_rejects_string_list_field():
    where = {"in": {"fieldName": "tags", "value": ["a"]}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_contains_only_for_string_list():
    where = {"contains": {"fieldName": "status", "value": "x"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_contains_value_must_be_single_string():
    where = {"contains": {"fieldName": "tags", "value": ["a", "b"]}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_exists_must_not_carry_value():
    where = {"exists": {"fieldName": "status", "value": "x"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_gt_rejects_string_field():
    where = {"gt": {"fieldName": "status", "value": "x"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_gt_accepts_number_and_datetime():
    validate_where_clause(
        {"gt": {"fieldName": "priority", "value": 3}}, known_fields=KNOWN_FIELDS
    )
    validate_where_clause(
        {"gt": {"fieldName": "effectiveAt", "value": "2026-01-01T00:00:00Z"}},
        known_fields=KNOWN_FIELDS,
    )


def test_eq_requires_value_key():
    where = {"eq": {"fieldName": "status"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_system_field_in_known_fields_is_recognized():
    """Callers merge SYSTEM_FIELD_VALUE_TYPES into known_fields; verify it works."""
    from by_qa.knowledge_base.metadata_types import SYSTEM_FIELD_VALUE_TYPES

    known = dict(SYSTEM_FIELD_VALUE_TYPES) | KNOWN_FIELDS
    where = {"eq": {"fieldName": "fileName", "value": "report.md"}}
    validate_where_clause(where, known_fields=known)


def test_system_field_size_enforces_number_type():
    from by_qa.knowledge_base.metadata_types import SYSTEM_FIELD_VALUE_TYPES

    known = dict(SYSTEM_FIELD_VALUE_TYPES) | KNOWN_FIELDS
    where = {"gt": {"fieldName": "fileSize", "value": "ten"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=known)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


# --- prefix operator ---


def test_prefix_valid_on_string_field():
    where = {"prefix": {"fieldName": "status", "value": "act"}}
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_prefix_rejects_on_non_string_field():
    where = {"prefix": {"fieldName": "priority", "value": "1"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_prefix_value_must_be_string():
    where = {"prefix": {"fieldName": "status", "value": 123}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_prefix_requires_value():
    where = {"prefix": {"fieldName": "status"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


# --- wildcard operator ---


def test_wildcard_valid_on_string_field():
    where = {"wildcard": {"fieldName": "status", "value": "act*"}}
    validate_where_clause(where, known_fields=KNOWN_FIELDS)


def test_wildcard_rejects_on_non_string_field():
    where = {"wildcard": {"fieldName": "priority", "value": "1*"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_wildcard_value_must_be_string():
    where = {"wildcard": {"fieldName": "status", "value": ["a*"]}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"


def test_wildcard_requires_value():
    where = {"wildcard": {"fieldName": "status"}}
    with pytest.raises(DslValidationError) as exc_info:
        validate_where_clause(where, known_fields=KNOWN_FIELDS)
    assert exc_info.value.error_list[0].code == "INVALID_FIELD_VALUE_TYPE"
