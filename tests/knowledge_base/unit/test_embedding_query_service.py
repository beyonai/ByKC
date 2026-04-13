"""Tests for query embedding service error handling."""

import httpx
import pytest

from by_qa.knowledge_base.services.embedding_query_service import EmbeddingQueryService
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


def _make_service() -> EmbeddingQueryService:
    return EmbeddingQueryService(
        base_url="https://embedding.example.com",
        api_key="secret",
        model_name="bge-m3",
    )


def test_embed_query_wraps_http_errors_as_configuration_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    """Embedding transport failures should surface as stable knowledge-base errors."""
    service = _make_service()

    def _fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        del url, headers, json, timeout
        raise httpx.HTTPError("connection failed")

    monkeypatch.setattr(
        "by_qa.knowledge_base.services.embedding_query_service.httpx.post",
        _fake_post,
    )

    with pytest.raises(
        KnowledgeBaseConfigurationError, match="embedding service request failed"
    ):
        service.embed_query("员工请假制度怎么规定")


def test_embed_query_rejects_missing_embedding_payload(monkeypatch: pytest.MonkeyPatch):
    """Embedding responses without vectors should still raise a stable config error."""
    service = _make_service()

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{}]}

    monkeypatch.setattr(
        "by_qa.knowledge_base.services.embedding_query_service.httpx.post",
        lambda url, *, headers, json, timeout: _FakeResponse(),
    )

    with pytest.raises(
        KnowledgeBaseConfigurationError,
        match="embedding response did not include data\\[0\\]\\.embedding",
    ):
        service.embed_query("员工请假制度怎么规定")
