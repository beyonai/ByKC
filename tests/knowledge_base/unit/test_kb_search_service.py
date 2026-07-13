"""Tests for KB hybrid retrieval service behavior."""

from __future__ import annotations

import pytest

from by_qa.knowledge_base.api.schemas import SearchRequest
from by_qa.knowledge_base.services.knowledge_item_search_service import (
    KnowledgeItemSearchService,
    _merge_file_type_into_where,
)


class _FakeConnection:
    def cursor(self):
        return object()

    async def close(self):
        return None


async def _fake_connection_factory():
    return _FakeConnection()


class _FakeEmbeddingQueryService:
    async def embed_query(self, query: str):  # pylint: disable=unused-argument
        return [0.1, 0.2, 0.3]


class _FakeSearchRepository:
    async def search_text(self, *args, **kwargs):  # pylint: disable=unused-argument
        return [
            {
                "chunk_id": 2,
                "knowledge_base_id": 1,
                "kb_code": "1",
                "full_path": "docs/current.md",
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 3,
                "chunk_text": "see byqa-ref://10",
                "fs_entry_id": 20,
                "text_score": 0.9,
            },
            {
                "chunk_id": 1,
                "knowledge_base_id": 1,
                "kb_code": "1",
                "full_path": "docs/current.md",
                "chunk_no": 2,
                "start_line": 4,
                "end_line": 5,
                "chunk_text": "again byqa-ref://10",
                "fs_entry_id": 20,
                "text_score": 0.5,
            },
        ]

    async def search_vector(self, *args, **kwargs):  # pylint: disable=unused-argument
        return []


class _FakeResolver:
    def __init__(self):
        self.calls = []

    async def resolve_texts(self, *, knowledge_base_id: int, texts: list[str]):
        self.calls.append((knowledge_base_id, list(texts)))
        return [text.replace("byqa-ref://10", "/docs/target.md") for text in texts]


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


@pytest.mark.asyncio
async def test_search_resolves_chunk_texts_in_batch_per_knowledge_base():
    resolver = _FakeResolver()
    service = KnowledgeItemSearchService(
        connection_factory=_fake_connection_factory,
        search_repository=_FakeSearchRepository(),
        embedding_query_service=_FakeEmbeddingQueryService(),
        markdown_reference_resolver=resolver,
    )

    results = await service.search(
        SearchRequest(
            query="hello",
            knCodeList=["1"],
            topK=2,
            searchMode="fullTextRecall",
        )
    )

    assert [hit.chunk_text for hit in results] == [
        "see /docs/target.md",
        "again /docs/target.md",
    ]
    assert resolver.calls == [
        (1, ["see byqa-ref://10", "again byqa-ref://10"]),
    ]
