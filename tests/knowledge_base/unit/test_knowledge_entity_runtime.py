"""Runtime composition tests for KnowledgeEntity processing."""

from types import SimpleNamespace

import pytest

from by_qa.core.model_config import LLMModelProfile
from by_qa.knowledge_base.infrastructure import runtime
from by_qa.knowledge_base.services.knowledge_entity_processing_service import (
    KnowledgeEntityProcessingOrchestrator,
)
from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskWorker,
)


@pytest.mark.asyncio
async def test_build_knowledge_entity_processing_service_composes_real_worker(
    monkeypatch,
):
    settings = object()
    connection_factory = object()
    chunker = object()
    storage = object()
    ingestion = object()
    document_update = object()
    search = object()
    calls = []
    embedding_config = SimpleNamespace(model_name="embedding", dimension=1024)

    class Provider:
        async def get_config(self, profile):
            calls.append(("get_config", profile))
            return embedding_config

    provider = Provider()

    monkeypatch.setattr(
        runtime, "validate_knowledge_base_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        runtime, "build_connection_factory", lambda value: connection_factory
    )

    async def fake_storage(value, *, embedding_config=None):
        calls.append(("storage", value, embedding_config))
        return storage

    async def fake_ingestion(value, provider=None, *, event_publisher_invoker=None):
        calls.append(("ingestion", value, provider, event_publisher_invoker))
        return ingestion

    async def fake_update(value, provider=None):
        calls.append(("update", value, provider))
        return document_update

    async def fake_search(value, provider=None):
        calls.append(("search", value, provider))
        return search

    monkeypatch.setattr(runtime, "build_storage_provider", fake_storage)
    monkeypatch.setattr(
        runtime, "build_knowledge_item_ingestion_service", fake_ingestion
    )
    monkeypatch.setattr(runtime, "build_document_update_service", fake_update)
    monkeypatch.setattr(runtime, "build_knowledge_item_search_service", fake_search)

    service = await runtime.build_knowledge_entity_processing_service(
        settings,
        provider,
        document_chunking_service=chunker,
    )

    assert isinstance(service, KnowledgeEntityProcessingOrchestrator)
    assert isinstance(service.worker, KnowledgeEntityTaskWorker)
    assert service.connection_factory is connection_factory
    assert service.worker._connection_factory is connection_factory
    assert service.worker._storage is storage
    assert service.worker._ingestion is ingestion
    assert service.worker._document_update is document_update
    assert service.worker._chunker is chunker
    assert service.worker._search is search
    assert service.worker._discovery._llm is not service.worker._enricher._llm
    assert service.worker._discovery._llm._provider is provider
    assert service.worker._enricher._llm._provider is provider
    assert service.worker._discovery._llm._temperature == 0.0
    assert service.worker._enricher._llm._temperature == 0.0
    assert service.worker._enricher._llm._request_extra_body == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }
    assert calls[:2] == [
        ("get_config", LLMModelProfile.EMBEDDING),
        ("storage", settings, embedding_config),
    ]
    assert calls[2][:3] == ("ingestion", settings, provider)
    assert calls[2][3] is service.event_publisher_invoker
    assert calls[3:] == [
        ("update", settings, provider),
        ("search", settings, provider),
    ]
