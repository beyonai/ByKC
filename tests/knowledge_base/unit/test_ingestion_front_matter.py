"""Unit tests for YAML front matter auto-metadata during file upload."""

from __future__ import annotations

from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    _parse_front_matter,
)


def test_parse_front_matter_valid():
    content = b"---\ntitle: Hello\ntags:\n  - a\n  - b\n---\n# Body\n"
    result = _parse_front_matter(content)
    assert result == {"title": "Hello", "tags": ["a", "b"]}


def test_parse_front_matter_no_header():
    content = b"# Just a heading\nSome text."
    result = _parse_front_matter(content)
    assert result == {}


def test_parse_front_matter_empty_header():
    content = b"---\n---\n# Body\n"
    result = _parse_front_matter(content)
    assert result == {}


def test_parse_front_matter_invalid_yaml():
    content = b"---\n: bad: yaml: [unclosed\n---\n# Body\n"
    result = _parse_front_matter(content)
    assert result == {}
