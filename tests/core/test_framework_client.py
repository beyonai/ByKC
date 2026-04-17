"""Tests for discovered HTTP service client helpers."""

from types import SimpleNamespace

import pytest

from by_qa.core import framework_client


class FakeDiscoveryClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeDiscoveryHttpClient:
    recorded_request: dict | None = None

    def __init__(self, discovery_client, *, retry_config):
        self.discovery_client = discovery_client
        self.retry_config = retry_config

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    async def _request_with_discovery(self, **kwargs):
        self.__class__.recorded_request = kwargs
        return SimpleNamespace(is_success=True, data={"ok": True})


@pytest.mark.asyncio
async def test_request_discovered_json_forwards_headers(monkeypatch):
    discovery_client = FakeDiscoveryClient()
    monkeypatch.setattr(
        framework_client,
        "_build_discovery_client",
        lambda: discovery_client,
    )
    monkeypatch.setattr(
        framework_client,
        "DiscoveryHttpClient",
        FakeDiscoveryHttpClient,
    )

    result = await framework_client.request_discovered_json(
        method="POST",
        service_name="kb-search-service-a",
        path="/api/v1/knowledgeItems/search",
        json={"query": "员工请假制度"},
        headers={"Authorization": "Bearer hr-token"},
    )

    assert result == {"ok": True}
    assert FakeDiscoveryHttpClient.recorded_request == {
        "method": "POST",
        "service_name": "kb-search-service-a",
        "path": "/api/v1/knowledgeItems/search",
        "json": {"query": "员工请假制度"},
        "headers": {"Authorization": "Bearer hr-token"},
    }
    assert discovery_client.closed is True
