"""Tests for canonical KnowledgeEntity document metadata defaults."""

from pathlib import Path

from by_qa.knowledge_base.services.knowledge_document_metadata import (
    default_document_kind_for_path,
    ensure_document_kind_metadata,
)


class MetadataRepository:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.upserts = []

    async def get_file_metadata(self, cursor, *, fs_entry_id, property_names=None):
        del cursor
        return [
            row
            for row in self.rows
            if row["fs_entry_id"] == fs_entry_id
            and (property_names is None or row["property_name"] in property_names)
        ]

    async def upsert_value(self, cursor, **kwargs):
        del cursor
        self.upserts.append(kwargs)
        self.rows.append(kwargs)
        return {"kid": len(self.rows)}


def test_document_kind_default_uses_segment_safe_reserved_directory():
    assert default_document_kind_for_path("/KnowledgeEntity") == "knowledgeEntity"
    assert (
        default_document_kind_for_path("KnowledgeEntity/platform.md")
        == "knowledgeEntity"
    )
    assert default_document_kind_for_path("/knowledgeentity/a.md") == "original"
    assert default_document_kind_for_path("/KnowledgeEntity2/a.md") == "original"
    assert default_document_kind_for_path("/docs/a.md") == "original"


async def test_ensure_document_kind_inserts_only_when_active_value_is_missing():
    repository = MetadataRepository()

    inserted = await ensure_document_kind_metadata(
        object(),
        file_metadata_value_repository=repository,
        fs_entry_id=8,
        knowledge_base_id=7,
        file_path="/docs/a.md",
    )
    repeated = await ensure_document_kind_metadata(
        object(),
        file_metadata_value_repository=repository,
        fs_entry_id=8,
        knowledge_base_id=7,
        file_path="/docs/a.md",
    )

    assert inserted is True
    assert repeated is False
    assert repository.upserts == [
        {
            "fs_entry_id": 8,
            "knowledge_base_id": 7,
            "property_name": "documentKind",
            "value_type": "string",
            "value": "original",
        }
    ]


async def test_ensure_document_kind_preserves_any_explicit_active_value():
    repository = MetadataRepository(
        [
            {
                "fs_entry_id": 8,
                "property_name": "documentKind",
                "value_type": "string",
                "value": "knowledgeEntity",
            }
        ]
    )

    inserted = await ensure_document_kind_metadata(
        object(),
        file_metadata_value_repository=repository,
        fs_entry_id=8,
        knowledge_base_id=7,
        file_path="/docs/a.md",
    )

    assert inserted is False
    assert repository.upserts == []


def test_document_kind_backfill_is_additive_idempotent_and_non_overwriting():
    sql = Path(
        "src/by_qa/knowledge_base/sql/031_knowledge_file_document_kind_backfill.sql"
    ).read_text(encoding="utf-8")

    assert "WHERE file_entry.entry_type = 'FILE'" in sql
    assert "NOT EXISTS" in sql
    assert "existing.property_name = 'documentKind'" in sql
    assert "existing.is_deleted = FALSE" in sql
    assert "existing.value_type" not in sql
    assert "UPDATE knowledge_file_metadata_value" not in sql
    assert "file_entry.is_deleted = FALSE" in sql
    assert "file_entry.virtual_path = '/KnowledgeEntity'" in sql
    assert "file_entry.virtual_path LIKE '/KnowledgeEntity/%'" in sql
    assert "THEN 'knowledgeEntity'" in sql
    assert "ELSE 'original'" in sql
