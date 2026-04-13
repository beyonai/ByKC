"""Tests for document chunking service file type handling."""

from by_qa.knowledge_build.services.document_chunking_service import (
    DocumentChunkingService,
)


def _make_service() -> DocumentChunkingService:
    """Create a service instance without exercising embedding calls."""
    return DocumentChunkingService(
        embedding_base_url="http://example.com",
        embedding_api_key="test-key",
        embedding_model_name="test-model",
        embedding_dimension=3,
    )


def test_extract_text_from_file_accepts_text_types_case_insensitively():
    """Direct service callers should get case-insensitive text type handling."""
    service = _make_service()

    assert service.extract_text_from_file(b"hello", "TXT") == "hello"
    assert service.extract_text_from_file(b"# title", "Md") == "# title"
    assert service.extract_text_from_file(b"# title", "markdown") == "# title"
    assert (
        service.extract_text_from_file(b"name,age\nalice,18\n", "CSV")
        == "name | age\nalice | 18"
    )
