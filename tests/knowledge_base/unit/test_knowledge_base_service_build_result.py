"""Tests for the knowledge-base build-result aggregation service."""

from datetime import datetime, timedelta, timezone

import pytest

from by_qa.knowledge_base.api.schemas import BuildPreviewRequest, BuildResultRequest
from by_qa.knowledge_base.infrastructure.storage import StorageOperationError
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


class FakeConnection:
    def __init__(self):
        self.closed = False
        self._cursor = object()

    def cursor(self):
        return self._cursor

    async def close(self):
        self.closed = True


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor, kb_code):
        return {"kid": 7, "kb_code": kb_code}


class FakeFsEntryRepository:
    async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
        assert knowledge_base_id == 7
        assert full_path == "slides/demo.pptx"
        return {
            "kid": 71,
            "name": "demo.pptx",
            "file_size": 2048,
            "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "markdown_bucket_name": "kb",
            "markdown_object_key": "demo.md",
            "line_count": 4,
        }


class FakeBuildTaskRepository:
    async def get_latest_by_fs_entry_id(self, cursor, *, fs_entry_id):
        assert fs_entry_id == 71
        started = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        return {
            "status": "complete",
            "current_step": "complete",
            "error_message": None,
            "started_at": started,
            "finished_at": started + timedelta(milliseconds=1250),
        }


class FakeChunkRepository:
    async def get_build_result_summary(self, cursor, *, fs_entry_id):
        assert fs_entry_id == 71
        return {
            "chunk_count": 3,
            "embedded_chunk_count": 3,
            "indexed_chunk_count": 2,
        }

    async def list_build_result_chunks(self, cursor, *, fs_entry_id, offset, limit):
        assert (fs_entry_id, offset, limit) == (71, 0, 2)
        return [
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 2,
                "chunk_text": "# Demo\nFirst",
                "has_embedding": True,
                "retrieval_indexed": True,
            },
            {
                "chunk_no": 2,
                "start_line": 3,
                "end_line": 4,
                "chunk_text": "Second",
                "has_embedding": True,
                "retrieval_indexed": True,
            },
        ]


class FakeStorageProvider:
    def build_markdown_location(self, **kwargs):
        return type("Location", (), {"namespace": "kb", "key": "demo.md"})()

    async def read(self, location):
        if location.key == "demo.md":
            return b"# Demo\nFirst\nSecond\nEnd"
        assert (location.namespace, location.key) == ("kb", "demo.preview.pdf")
        return b"%PDF-1.7\npreview"


@pytest.mark.asyncio
async def test_build_result_aggregates_markdown_chunks_and_index_coverage():
    connection = FakeConnection()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=FakeFsEntryRepository(),
        knowledge_build_task_repository=FakeBuildTaskRepository(),
        knowledge_item_chunk_repository=FakeChunkRepository(),
        embedding_dimension=1024,
        storage_provider=FakeStorageProvider(),
    )

    result = await service.build_result(
        BuildResultRequest(
            knCode="7",
            filePath="/slides/demo.pptx",
            chunkPage=1,
            chunkPageSize=2,
        )
    )

    assert result["fileType"] == "pptx"
    assert result["build"]["durationMs"] == 1250
    assert result["markdown"] == {
        "available": True,
        "data": "# Demo\nFirst\nSecond\nEnd",
        "lineCount": 4,
        "characterCount": 23,
        "byteCount": 23,
    }
    assert result["chunks"]["total"] == 3
    assert result["chunks"]["reachedEof"] is False
    assert result["chunks"]["data"][0]["hasEmbedding"] is True
    assert result["embedding"] == {
        "dimension": 1024,
        "embeddedChunkCount": 3,
        "coverageRate": 100.0,
    }
    assert result["retrieval"] == {
        "indexedChunkCount": 2,
        "coverageRate": 66.67,
    }
    assert connection.closed is True


@pytest.mark.asyncio
async def test_build_preview_reads_pdf_sidecar():
    connection = FakeConnection()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=FakeFsEntryRepository(),
        storage_provider=FakeStorageProvider(),
    )

    content = await service.build_preview(
        BuildPreviewRequest(knCode="7", filePath="/slides/demo.pptx")
    )

    assert content == b"%PDF-1.7\npreview"
    assert connection.closed is True


@pytest.mark.asyncio
async def test_build_preview_normalizes_storage_operation_failure():
    class FailingStorageProvider(FakeStorageProvider):
        async def read(self, location):
            raise StorageOperationError("userfs returned 500")

    connection = FakeConnection()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=FakeFsEntryRepository(),
        storage_provider=FailingStorageProvider(),
    )

    with pytest.raises(KnowledgeBaseValidationError, match="rebuild the PPT/PPTX"):
        await service.build_preview(
            BuildPreviewRequest(knCode="7", filePath="/slides/demo.pptx")
        )

    assert connection.closed is True


async def _async_return(value):
    return value
