"""Tests for DSL guide content and Agent-facing SearchInput metadata fields."""

from by_qa.qa.common.operation_registry import (
    OPERATION_REGISTRY,
    DslGuideInput,
    OperationType,
    SearchInput,
)
from by_qa.qa.tools.dsl_guide import DSL_GUIDE_CONTENT


def test_dsl_guide_content_has_required_sections():
    assert "Agent DSL Syntax Reference" in DSL_GUIDE_CONTENT
    assert "Boolean Operators" in DSL_GUIDE_CONTENT
    assert "Leaf Operators" in DSL_GUIDE_CONTENT
    assert "Limits" in DSL_GUIDE_CONTENT
    assert "Examples" in DSL_GUIDE_CONTENT
    for type_name in ("string", "stringList", "number", "boolean", "datetime"):
        assert type_name in DSL_GUIDE_CONTENT


def test_dsl_guide_content_describes_nesting_and_leaf_limits():
    assert "nesting depth: 3" in DSL_GUIDE_CONTENT
    assert "leaf conditions: 12" in DSL_GUIDE_CONTENT


def test_dsl_guide_operation_registry_tool_name():
    spec = OPERATION_REGISTRY[OperationType.DSL_GUIDE]
    assert spec.tool_name == "get_dsl_guide"


def test_dsl_guide_input_is_no_arg():
    inp = DslGuideInput()
    assert inp.model_dump() == {}


def test_search_input_does_not_expose_where():
    inp = SearchInput.model_validate(
        {"query": "test", "where": {"eq": {"fieldName": "status", "value": "active"}}}
    )
    assert "where" not in SearchInput.model_fields
    assert "where" not in inp.model_dump()


def test_search_input_accepts_metadata_field_list():
    inp = SearchInput.model_validate(
        {"query": "test", "metadataFieldList": ["status", "tags"]}
    )
    assert inp.metadata_field_list == ["status", "tags"]


def test_search_input_metadata_field_list_defaults_to_none():
    inp = SearchInput.model_validate({"query": "test"})
    assert inp.metadata_field_list is None
