"""Tests for the file-count HTTP contract, including actual error status codes."""

from unittest.mock import AsyncMock

import pytest
from test_kb_api_routes import FakeKBService, make_test_client

from by_qa.knowledge_base.services.errors import (
    KnowledgeBaseConfigurationError,
    KnowledgeBaseValidationError,
)


@pytest.mark.parametrize("count", [0, 12])
def test_knowledge_base_file_count_route_returns_count(monkeypatch, count):
    service = FakeKBService()
    service.file_count = AsyncMock(return_value={"fileCount": count})
    client = make_test_client(monkeypatch, service)

    response = client.post("/api/v1/knowledgeBases/fileCount", json={"knCode": "123"})

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {"fileCount": count},
    }
    assert service.file_count.await_args.args[0].kb_code == "123"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b"\xff",
        b"null",
        b"[]",
        b'"123"',
        b"{}",
        b'{"knCode": ""}',
        b'{"knCode": null}',
        b'{"knCode": 123}',
    ],
)
def test_knowledge_base_file_count_route_rejects_invalid_body(monkeypatch, body):
    service = FakeKBService()
    service.file_count = AsyncMock()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeBases/fileCount",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "request validation failed",
        "resultObject": {},
    }
    service.file_count.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (KnowledgeBaseValidationError("knowledge base not found: 123"), 422),
        (KnowledgeBaseConfigurationError("database is not configured"), 503),
        (RuntimeError("database unavailable"), 500),
    ],
)
def test_knowledge_base_file_count_route_preserves_error_status(
    monkeypatch, error, status_code
):
    service = FakeKBService()
    service.file_count = AsyncMock(side_effect=error)
    client = make_test_client(monkeypatch, service)

    response = client.post("/api/v1/knowledgeBases/fileCount", json={"knCode": "123"})

    assert response.status_code == status_code
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": str(error),
        "resultObject": {},
    }
