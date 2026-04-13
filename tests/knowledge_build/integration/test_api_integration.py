"""User-journey oriented integration tests for knowledge_build APIs."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from by_qa.knowledge_common.exceptions import KnowledgeConfigurationError
from by_qa.main import create_app


class FakeDocumentChunkingService:
    """Controllable document chunking double for API integration tests."""

    def __init__(
        self,
        *,
        markdown_result: str = "# Extracted\n\ncontent",
        chunks_result: list[dict] | None = None,
        parse_exc: Exception | None = None,
        chunk_exc: Exception | None = None,
    ) -> None:
        self.markdown_result = markdown_result
        self.chunks_result = chunks_result or [
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 2,
                "chunk_text": markdown_result,
                "embedding": [0.1, 0.2],
                "char_start": 0,
                "char_end": len(markdown_result.encode("utf-8")),
            }
        ]
        self.parse_exc = parse_exc
        self.chunk_exc = chunk_exc
        self.calls: list[tuple[str, str]] = []

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        assert isinstance(file_bytes, bytes)
        self.calls.append(("extract", file_type))
        if self.parse_exc is not None:
            raise self.parse_exc
        return self.markdown_result

    def chunk_and_embed(self, file_bytes: bytes, *, filename: str) -> list[dict]:
        assert isinstance(file_bytes, bytes)
        self.calls.append(("chunk", filename))
        if self.chunk_exc is not None:
            raise self.chunk_exc
        return self.chunks_result


def make_test_client(
    monkeypatch: pytest.MonkeyPatch,
    service: FakeDocumentChunkingService | None,
) -> TestClient:
    """Create a client with the document chunking service monkeypatched."""
    monkeypatch.setattr("by_qa.main.get_document_chunking_service", lambda: service)
    return TestClient(create_app())


@pytest.mark.integration
def test_file_to_markdown_supports_success_and_common_failures(monkeypatch):
    """User can submit one file for parsing and receive stable success/error envelopes."""
    payload = base64.b64encode(b"fake-pdf-content").decode("ascii")

    success_client = make_test_client(monkeypatch, FakeDocumentChunkingService())
    success = success_client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "pdf"},
    )
    assert success.status_code == 200
    assert success.json()["data"]["md_content"] == "# Extracted\n\ncontent"

    text_payload = base64.b64encode(b"plain text content").decode("ascii")
    txt_success = success_client.post(
        "/api/v1/file-to-markdown",
        json={"content": text_payload, "type": "TXT"},
    )
    assert txt_success.status_code == 200
    assert txt_success.json()["data"]["md_content"] == "# Extracted\n\ncontent"

    markdown_payload = base64.b64encode(b"# Title\n\nbody").decode("ascii")
    markdown_success = success_client.post(
        "/api/v1/file-to-markdown",
        json={"content": markdown_payload, "type": "Md"},
    )
    assert markdown_success.status_code == 200
    assert markdown_success.json()["data"]["md_content"] == "# Extracted\n\ncontent"

    csv_payload = base64.b64encode(b"name,age\nalice,18\n").decode("ascii")
    csv_success = success_client.post(
        "/api/v1/file-to-markdown",
        json={"content": csv_payload, "type": "csv"},
    )
    assert csv_success.status_code == 200
    assert csv_success.json()["data"]["md_content"] == "# Extracted\n\ncontent"

    unsupported = success_client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "exe"},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["error_code"] == "FILE_TYPE_UNSUPPORTED"

    invalid_base64 = success_client.post(
        "/api/v1/file-to-markdown",
        json={"content": "not-base64", "type": "pdf"},
    )
    assert invalid_base64.status_code == 422
    assert invalid_base64.json()["error"]["error_code"] == "FILE_CONTENT_INVALID"

    config_client = make_test_client(monkeypatch, None)
    config_error = config_client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "pdf"},
    )
    assert config_error.status_code == 503
    assert config_error.json()["error"]["error_code"] == "RUNTIME_CONFIG_ERROR"


@pytest.mark.integration
def test_build_markdown_index_supports_success_and_dependency_failures(monkeypatch):
    """User can build chunks from markdown and see validation/dependency failures clearly."""
    client = make_test_client(monkeypatch, FakeDocumentChunkingService())

    success = client.post(
        "/api/v1/build-markdown-index",
        json={"content": "# Title\n\ncontent"},
    )
    assert success.status_code == 200
    assert success.json()["data"]["chunks"][0]["chunk_text"] == "# Extracted\n\ncontent"

    empty_content = client.post(
        "/api/v1/build-markdown-index",
        json={"content": "   \n\t"},
    )
    assert empty_content.status_code == 422
    assert empty_content.json()["error"]["error_code"] == "CHUNK_EMPTY"

    embedding_client = make_test_client(
        monkeypatch,
        FakeDocumentChunkingService(
            chunk_exc=KnowledgeConfigurationError("embedding service is unavailable")
        ),
    )
    embedding_error = embedding_client.post(
        "/api/v1/build-markdown-index",
        json={"content": "# Title\n\ncontent"},
    )
    assert embedding_error.status_code == 503
    assert embedding_error.json()["error"]["error_code"] == "EMBEDDING_SERVICE_ERROR"


@pytest.mark.integration
def test_file_to_markdown_index_matches_the_two_step_pipeline(monkeypatch):
    """User can choose one-step or two-step build flows and get the same result."""
    payload = base64.b64encode(b"fake-pdf-content").decode("ascii")
    service = FakeDocumentChunkingService(
        markdown_result="# Heading\n\nBody",
        chunks_result=[
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 2,
                "chunk_text": "# Heading\n\nBody",
                "embedding": [0.1, 0.2],
                "char_start": 0,
                "char_end": 15,
            }
        ],
    )
    client = make_test_client(monkeypatch, service)

    markdown_only = client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "pdf"},
    )
    build_only = client.post(
        "/api/v1/build-markdown-index",
        json={"content": markdown_only.json()["data"]["md_content"]},
    )
    combined = client.post(
        "/api/v1/file-to-markdown-index",
        json={"content": payload, "type": "pdf"},
    )

    assert markdown_only.status_code == 200
    assert build_only.status_code == 200
    assert combined.status_code == 200
    assert (
        combined.json()["data"]["md_content"]
        == markdown_only.json()["data"]["md_content"]
    )
    assert combined.json()["data"]["chunks"] == build_only.json()["data"]["chunks"]


@pytest.mark.integration
def test_file_to_markdown_index_short_circuits_on_stage_errors(monkeypatch):
    """Combined flow should stop at the failing stage instead of producing partial output."""
    payload = base64.b64encode(b"fake-pdf-content").decode("ascii")

    parse_client = make_test_client(
        monkeypatch,
        FakeDocumentChunkingService(parse_exc=ValueError("parser exploded")),
    )
    parse_error = parse_client.post(
        "/api/v1/file-to-markdown-index",
        json={"content": payload, "type": "pdf"},
    )
    assert parse_error.status_code == 422
    assert parse_error.json()["error"]["error_code"] == "FILE_PARSE_FAILED"

    chunk_client = make_test_client(
        monkeypatch,
        FakeDocumentChunkingService(chunk_exc=ValueError("markdown content is empty")),
    )
    chunk_error = chunk_client.post(
        "/api/v1/file-to-markdown-index",
        json={"content": payload, "type": "pdf"},
    )
    assert chunk_error.status_code == 422
    assert chunk_error.json()["error"]["error_code"] == "CHUNK_EMPTY"
