"""Unit tests for YAML front matter auto-metadata during file upload."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from by_qa.knowledge_base.metadata_types import prepare_front_matter_metadata_value
from by_qa.knowledge_base.services.entry_metadata import (
    merge_entry_metadata,
    upsert_entry_metadata,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-18", date(2026, 8, 18)),
        (
            "2026-08-18T10:30:00+08:00",
            datetime.fromisoformat("2026-08-18T10:30:00+08:00"),
        ),
        (
            "2026-08-18T02:30:00Z",
            datetime(2026, 8, 18, 2, 30, tzinfo=timezone.utc),
        ),
    ],
)
def test_prepare_front_matter_metadata_value_coerces_quoted_iso_datetime(
    value, expected
):
    assert prepare_front_matter_metadata_value(value) == ("datetime", expected)


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30",
        "2026-08-18 draft",
        " 2026-08-18 ",
        "v2026-08-18",
    ],
)
def test_prepare_front_matter_metadata_value_preserves_date_like_strings(value):
    assert prepare_front_matter_metadata_value(value) == ("string", value)


@pytest.mark.asyncio
async def test_ingestion_stores_quoted_iso_date_as_datetime_metadata():
    class MetadataRepository:
        def __init__(self):
            self.calls = []

        async def upsert_value(self, cursor, **kwargs):
            self.calls.append(kwargs)

    repository = MetadataRepository()
    await upsert_entry_metadata(
        object(),
        metadata_repository=repository,
        fs_entry_id=11,
        knowledge_base_id=7,
        metadata=parse_front_matter(
            b'---\ntitle: "AI systems"\npublished_date: "2026-08-18"\n---\n'
        ),
    )

    assert repository.calls == [
        {
            "fs_entry_id": 11,
            "knowledge_base_id": 7,
            "property_name": "title",
            "value_type": "string",
            "value": "AI systems",
        },
        {
            "fs_entry_id": 11,
            "knowledge_base_id": 7,
            "property_name": "published_date",
            "value_type": "datetime",
            "value": date(2026, 8, 18),
        },
    ]


def test_request_metadata_is_merged_before_front_matter():
    assert merge_entry_metadata(
        {"owner": "request", "requestOnly": True},
        {"owner": "front matter", "frontOnly": 1},
    ) == {
        "owner": "front matter",
        "requestOnly": True,
        "frontOnly": 1,
    }


@pytest.mark.asyncio
async def test_shared_entry_metadata_rejects_read_only_system_fields_before_writes():
    class MetadataRepository:
        def __init__(self):
            self.calls = []

        async def upsert_value(self, cursor, **kwargs):
            self.calls.append(kwargs)

    repository = MetadataRepository()

    with pytest.raises(
        KnowledgeBaseValidationError,
        match="metadata field is read-only: fileName",
    ):
        await upsert_entry_metadata(
            object(),
            metadata_repository=repository,
            fs_entry_id=11,
            knowledge_base_id=7,
            metadata={"owner": "Alice", "fileName": "forged.md"},
        )

    assert repository.calls == []
