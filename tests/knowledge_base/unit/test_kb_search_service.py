"""Tests for KB hybrid retrieval service behavior."""

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import KnowledgeItemSearchRequest
from by_qa.knowledge_base.services.knowledge_item_search_service import (
    KnowledgeItemSearchService,
)


class FakeEmbeddingQueryService:
    """Embedding service test double."""

    def __init__(self, embedding=None):
        self.embedding = embedding or [0.1, 0.2, 0.3]
        self.queries = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return self.embedding


class FakeSearchRepository:
    """Repository double exposing deterministic text/vector hits."""

    def __init__(self):
        self.text_calls = []
        self.vector_calls = []

    def search_text(self, cursor, *, query, kb_codes, source_codes, type_codes, limit):
        self.text_calls.append(
            {
                "query": query,
                "kb_codes": kb_codes,
                "source_codes": source_codes,
                "type_codes": type_codes,
                "limit": limit,
            }
        )
        return [
            {
                "chunk_id": 100,
                "kb_code": "hr-policy",
                "knowledge_item_id": 10,
                "item_code": "item-1",
                "full_path": "/employee-handbook.md",
                "version": "v2",
                "source_code": "oa",
                "type_code": "policy_markdown",
                "title": "员工手册.pdf",
                "chunk_no": 1,
                "chunk_text": "员工请假应至少提前一天提交申请。",
                "text_score": 0.7,
            }
        ]

    def search_vector(
        self, cursor, *, query_embedding, kb_codes, source_codes, type_codes, limit
    ):
        self.vector_calls.append(
            {
                "query_embedding": query_embedding,
                "kb_codes": kb_codes,
                "source_codes": source_codes,
                "type_codes": type_codes,
                "limit": limit,
            }
        )
        return [
            {
                "chunk_id": 100,
                "kb_code": "hr-policy",
                "knowledge_item_id": 10,
                "item_code": "item-1",
                "full_path": "/employee-handbook.md",
                "version": "v2",
                "source_code": "oa",
                "type_code": "policy_markdown",
                "title": "员工手册.pdf",
                "chunk_no": 1,
                "chunk_text": "员工请假应至少提前一天提交申请。",
                "vector_score": 0.8,
            },
            {
                "chunk_id": 101,
                "kb_code": "hr-policy",
                "knowledge_item_id": 11,
                "item_code": "item-2",
                "full_path": "/attendance-policy.md",
                "version": "v1",
                "source_code": "oa",
                "type_code": "policy_markdown",
                "title": "考勤制度.pdf",
                "chunk_no": 2,
                "chunk_text": "员工补卡需要主管审批。",
                "vector_score": 0.5,
            },
        ]


class FakeConnection:
    """Connection double with a cursor method."""

    def cursor(self):
        return object()

    def close(self):
        return None


def test_search_service_merges_text_and_vector_hits_into_chunk_results():
    """Hybrid search should deduplicate by chunk id and keep both score channels."""
    service = KnowledgeItemSearchService(
        connection_factory=lambda: FakeConnection(),
        search_repository=FakeSearchRepository(),
        embedding_query_service=FakeEmbeddingQueryService(),
    )

    response = service.search(
        KnowledgeItemSearchRequest(
            query="员工请假制度怎么规定",
            kb_codes=["hr-policy"],
            top_k=10,
            vector_top_k=40,
            text_top_k=30,
        )
    )

    assert response.meta.returned_count == 2
    assert response.items[0].file_code == "item-1"
    assert response.items[0].file_path == "/employee-handbook.md"
    assert response.items[0].text_score == 0.7
    assert response.items[0].vector_score == 0.8
    assert response.items[1].file_code == "item-2"


def test_search_service_uses_request_candidate_limits_for_repository_calls():
    """Hybrid search should pass candidate pool sizes and filters to both recall paths."""
    repository = FakeSearchRepository()
    embedding = FakeEmbeddingQueryService()
    service = KnowledgeItemSearchService(
        connection_factory=lambda: FakeConnection(),
        search_repository=repository,
        embedding_query_service=embedding,
    )

    service.search(
        KnowledgeItemSearchRequest(
            query="员工请假制度怎么规定",
            kb_codes=["hr-policy"],
            top_k=5,
            vector_top_k=25,
            text_top_k=15,
            source_codes=["oa"],
            type_codes=["policy_markdown"],
        )
    )

    assert embedding.queries == ["员工请假制度怎么规定"]
    assert repository.text_calls[0]["limit"] == 15
    assert repository.vector_calls[0]["limit"] == 25
    assert repository.text_calls[0]["source_codes"] == ["oa"]
    assert repository.vector_calls[0]["type_codes"] == ["policy_markdown"]


def test_search_service_emits_internal_key_node_logs(monkeypatch):
    """Hybrid search should log embedding and retrieval key nodes."""
    repository = FakeSearchRepository()
    service = KnowledgeItemSearchService(
        connection_factory=lambda: FakeConnection(),
        search_repository=repository,
        embedding_query_service=FakeEmbeddingQueryService(),
    )
    info_messages: list[str] = []

    monkeypatch.setattr(
        logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )

    response = service.search(
        KnowledgeItemSearchRequest(
            query="员工请假制度怎么规定",
            kb_codes=["hr-policy"],
            top_k=5,
            vector_top_k=25,
            text_top_k=15,
        )
    )

    assert response.meta.returned_count == 2
    assert info_messages == [
        "knowledge_item_search_service.search started: query=员工请假制度怎么规定, kb_code_count=1, top_k=5, vector_top_k=25, text_top_k=15",
        "knowledge_item_search_service embedding started: query=员工请假制度怎么规定",
        "knowledge_item_search_service embedding finished: embedding_dimension=3",
        "knowledge_item_search_service hybrid retrieval started: text_limit=15, vector_limit=25",
        "knowledge_item_search_service hybrid retrieval finished: text_hit_count=1, vector_hit_count=2",
        "knowledge_item_search_service merge finished: merged_count=2",
        "knowledge_item_search_service search finished: returned_count=2",
    ]
