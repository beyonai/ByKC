"""Unit tests for YAML front matter auto-metadata during file upload."""

from __future__ import annotations

from by_qa.knowledge_base.services.markdown_front_matter import (
    parse_front_matter,
    split_front_matter,
)


def test_parse_front_matter_valid():
    content = b"---\ntitle: Hello\ntags:\n  - a\n  - b\n---\n# Body\n"
    result = parse_front_matter(content)
    assert result == {"title": "Hello", "tags": ["a", "b"]}


def test_split_front_matter_returns_body_without_yaml_block():
    content = b"---\ntitle: Hello\n---\n# Body\n"

    metadata, body = split_front_matter(content)

    assert metadata == {"title": "Hello"}
    assert body == b"# Body\n"


def test_parse_front_matter_no_header():
    content = b"# Just a heading\nSome text."
    result = parse_front_matter(content)
    assert result == {}


def test_parse_front_matter_empty_header():
    content = b"---\n---\n# Body\n"
    result = parse_front_matter(content)
    assert result == {}


def test_parse_front_matter_invalid_yaml():
    content = b"---\n: bad: yaml: [unclosed\n---\n# Body\n"
    result = parse_front_matter(content)
    assert result == {}


def test_split_front_matter_preserves_invalid_yaml_content():
    content = b"---\n: bad: yaml: [unclosed\n---\n# Body\n"

    metadata, body = split_front_matter(content)

    assert metadata == {}
    assert body == content
