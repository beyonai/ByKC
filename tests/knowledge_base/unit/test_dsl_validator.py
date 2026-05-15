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
