"""Tests for knowledge build API routes."""

import base64

from fastapi.testclient import TestClient

from by_qa.knowledge_build.api import routes
from by_qa.main import create_app


class FakeDocumentChunkingService:
    """Service double used by knowledge build route tests."""

    def extract_text_from_file(self, file_bytes, file_type):
        assert file_type in {"pdf", "txt", "md", "csv"}
        return f"# Extracted\n\nbytes={len(file_bytes)}"

    def chunk_and_embed(self, file_bytes, *, filename):
        assert filename == "input.md"
        return [
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 2,
                "chunk_text": file_bytes.decode("utf-8"),
                "embedding": [0.1, 0.2],
                "char_start": 0,
                "char_end": len(file_bytes),
            }
        ]


def make_test_client(monkeypatch, service):
    """Create a TestClient with the document chunking service stubbed."""
    monkeypatch.setattr("by_qa.main.get_document_chunking_service", lambda: service)
    return TestClient(create_app())


def test_file_to_markdown_route_returns_business_response(monkeypatch):
    """file-to-markdown should delegate to the document chunking service."""
    client = make_test_client(monkeypatch, FakeDocumentChunkingService())
    payload = base64.b64encode(b"fake-pdf-content").decode("utf-8")

    response = client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "pdf"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {"md_content": "# Extracted\n\nbytes=16"},
    }


def test_file_to_markdown_route_accepts_text_file_types_case_insensitively(monkeypatch):
    """file-to-markdown should accept added text file types without case sensitivity."""
    client = make_test_client(monkeypatch, FakeDocumentChunkingService())
    payload = base64.b64encode(b"name,age\nalice,18\n").decode("utf-8")

    response = client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "CSV"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["md_content"] == "# Extracted\n\nbytes=18"


def test_file_to_markdown_route_maps_markdown_type_to_md(monkeypatch):
    """file-to-markdown should normalize markdown requests to the md type label."""
    seen_file_types = []

    class RecordingDocumentChunkingService(FakeDocumentChunkingService):
        def extract_text_from_file(self, file_bytes, file_type):
            seen_file_types.append(file_type)
            assert file_type == "md"
            return super().extract_text_from_file(file_bytes, file_type)

    client = make_test_client(monkeypatch, RecordingDocumentChunkingService())
    payload = base64.b64encode(b"# title\n\nbody").decode("utf-8")

    response = client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "markdown"},
    )

    assert response.status_code == 200
    assert seen_file_types == ["md"]


def test_file_to_markdown_route_emits_summary_logs(monkeypatch):
    """file-to-markdown should emit request and response summary logs."""
    info_messages = []
    client = make_test_client(monkeypatch, FakeDocumentChunkingService())
    payload = base64.b64encode(b"fake-pdf-content").decode("utf-8")

    monkeypatch.setattr(
        routes.logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )

    response = client.post(
        "/api/v1/file-to-markdown",
        json={"content": payload, "type": "pdf"},
    )

    assert response.status_code == 200
    assert info_messages == [
        "file_to_markdown request received: type=pdf, content_length=24",
        "file_to_markdown response ready: code=200, type=pdf, md_content_length=21",
    ]


def test_build_markdown_index_route_returns_chunks(monkeypatch):
    """build-markdown-index should return chunk payloads."""
    client = make_test_client(monkeypatch, FakeDocumentChunkingService())

    response = client.post(
        "/api/v1/build-markdown-index",
        json={"content": "# Title\n\ncontent"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"]["chunks"][0]["chunk_text"] == "# Title\n\ncontent"


def test_file_to_markdown_index_route_combines_parse_and_chunk(monkeypatch):
    """file-to-markdown-index should return markdown plus chunk payloads."""
    client = make_test_client(monkeypatch, FakeDocumentChunkingService())
    payload = base64.b64encode(b"fake-pdf-content").decode("utf-8")

    response = client.post(
        "/api/v1/file-to-markdown-index",
        json={"content": payload, "type": "pdf"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["md_content"] == "# Extracted\n\nbytes=16"
    assert body["data"]["chunks"][0]["chunk_text"] == "# Extracted\n\nbytes=16"
