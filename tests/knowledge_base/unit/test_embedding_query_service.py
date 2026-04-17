"""Tests for query embedding service error handling."""

import httpx
import pytest

from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_base.services.embedding_query_service import EmbeddingQueryService
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


def _make_provider(
    base_url="https://embedding.example.com", api_key="secret", model_name="bge-m3"
):
    async def get_config(self, model_type: str) -> ModelConfig:  # pylint: disable=unused-argument
        return ModelConfig(
            model_name=model_name, temperature=0.0, base_url=base_url, api_key=api_key
        )

    return type("Provider", (), {"get_config": get_config})()


def _make_service() -> EmbeddingQueryService:
    return EmbeddingQueryService(provider=_make_provider())


async def test_embed_query_wraps_http_errors_as_configuration_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    """Embedding transport failures should surface as stable knowledge-base errors."""
    service = _make_service()

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):  # pylint: disable=unused-argument
            raise httpx.HTTPError("connection failed")

    monkeypatch.setattr(
        "by_qa.knowledge_base.services.embedding_query_service.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(),
    )

    with pytest.raises(
        KnowledgeBaseConfigurationError, match="embedding service request failed"
    ):
        await service.embed_query("员工请假制度怎么规定")


async def test_embed_query_rejects_missing_embedding_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    """Embedding responses without vectors should still raise a stable config error."""
    service = _make_service()

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{}]}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):  # pylint: disable=unused-argument
            return _FakeResponse()

    monkeypatch.setattr(
        "by_qa.knowledge_base.services.embedding_query_service.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(),
    )

    with pytest.raises(
        KnowledgeBaseConfigurationError,
        match="embedding response did not include data\\[0\\]\\.embedding",
    ):
        await service.embed_query("员工请假制度怎么规定")
