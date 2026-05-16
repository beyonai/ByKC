"""Tests for KB hybrid retrieval service behavior."""

from __future__ import annotations

from by_qa.knowledge_base.services.knowledge_item_search_service import (
    _merge_file_type_into_where,
)


def test_merge_file_type_when_where_absent_returns_in_clause():
    merged = _merge_file_type_into_where(None, ["md", "PDF"])
    assert merged == {"in": {"fieldName": "fileType", "value": ["md", "pdf"]}}


def test_merge_file_type_combines_with_existing_where():
    existing = {"eq": {"fieldName": "status", "value": "active"}}
    merged = _merge_file_type_into_where(existing, ["md"])
    assert merged == {
        "and": [
            existing,
            {"in": {"fieldName": "fileType", "value": ["md"]}},
        ]
    }


def test_merge_file_type_passthrough_when_no_file_type_list():
    existing = {"eq": {"fieldName": "status", "value": "active"}}
    assert _merge_file_type_into_where(existing, None) is existing
    assert _merge_file_type_into_where(None, None) is None


def test_merge_file_type_passthrough_when_empty_list():
    """Empty fileTypeList is treated as no filter (no clause appended)."""
    existing = {"eq": {"fieldName": "status", "value": "active"}}
    assert _merge_file_type_into_where(existing, []) is existing
