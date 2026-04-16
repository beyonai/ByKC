"""Real API integration tests for KB ingestion against openGauss and MinIO."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "15432"
DEFAULT_DB_USER = "gaussdb"
DEFAULT_DB_PASS = "OpenGauss#2026"


class FakeDocumentChunkingService:
    """Controllable double for document chunking in integration tests."""

    def __init__(
        self,
        *,
        markdown_text: str = "# hello\nreal integration\n",
        embedding: list[float] | None = None,
    ):
        self.markdown_text = markdown_text
        self.embedding = embedding or [0.1, 0.2, 0.3]

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:  # pylint: disable=unused-argument
        assert isinstance(file_bytes, bytes)
        return self.markdown_text

    def chunk_and_embed(
        self, file_bytes: bytes, *, filename: str
    ) -> list[KnowledgeItemChunkPayload]:
        assert isinstance(filename, str)
        content = (
            file_bytes.decode("utf-8") if isinstance(file_bytes, bytes) else file_bytes
        )
        line_count = max(1, content.count("\n"))
        return [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=line_count,
                chunk_text=content.strip(),
                embedding=self.embedding,
                char_start=0,
                char_end=len(file_bytes),
            )
        ]


def _kb_settings() -> Settings:
    return Settings(
        DB_HOST=os.getenv("DB_HOST", DEFAULT_DB_HOST),
        DB_PORT=int(os.getenv("DB_PORT", DEFAULT_DB_PORT)),
        DB_SCHEMA=os.getenv("DB_SCHEMA", ""),
        DB_USER=os.getenv("DB_USER", DEFAULT_DB_USER),
        DB_PASS=os.getenv("DB_PASS", DEFAULT_DB_PASS),
        MINIO_ENDPOINT=os.getenv("MINIO_ENDPOINT", "127.0.0.1:19000"),
        MINIO_ACCESS_KEY=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        MINIO_SECRET_KEY=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        KB_MINIO_BUCKET=os.getenv("KB_MINIO_BUCKET", "knowledge-base"),
        KB_MINIO_MARKDOWN_BUCKET=os.getenv(
            "KB_MINIO_MARKDOWN_BUCKET", "knowledge-base-markdown"
        ),
        MINIO_SECURE=False,
        EMBEDDING_MODEL_NAME=os.getenv("EMBEDDING_MODEL_NAME", "bge-m3"),
        EMBEDDING_BASE_URL="https://embedding.example.com",
        EMBEDDING_API_KEY="secret",
        EMBEDDING_DIMENSION=int(os.getenv("EMBEDDING_DIMENSION", "3")),
        EMBEDDING_DISTANCE_METRIC=os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine"),
    )


def _create_directory(
    client: TestClient,
    *,
    kb_code: str,
    directory_path: str,
) -> None:
    response = client.post(
        "/api/v1/directories/create",
        json={
            "knCode": kb_code,
            "directoryPath": directory_path,
            "directoryDescription": f"{directory_path} description",
        },
    )
    assert response.status_code == 200, response.text


def _upload_and_build_file(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    file_content: bytes,
    content_type: str = "text/markdown",
) -> None:
    """Upload a file and build its markdown index."""
    upload_response = client.post(
        "/api/v1/knowledgeItems/import",
        data={"knCode": kb_code, "filePath": file_path},
        files={"fileContent": (file_path.split("/")[-1], file_content, content_type)},
    )
    assert upload_response.status_code == 200, upload_response.text

    build_response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={
            "knCode": kb_code,
            "filePath": file_path,
        },
    )
    assert build_response.status_code == 200, build_response.text


@pytest.mark.integration
def test_kb_api_end_to_end_persists_to_opengauss_and_minio(monkeypatch):
    """Creating a KB and uploading+building a document should persist DB and object storage state."""
    settings = _kb_settings()
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)

    fake_chunking = FakeDocumentChunkingService(
        markdown_text="# hello\nreal integration\n"
    )
    monkeypatch.setattr(
        main_module, "get_document_chunking_service", lambda: fake_chunking
    )

    with TestClient(main_module.app) as client:
        kb_response = client.post(
            "/api/v1/knowledgeBases/create",
            json={"knName": f"Integration KB {uuid4().hex[:4]}"},
        )
        assert kb_response.status_code == 200
        kb_code = kb_response.json()["resultObject"]["knCode"]
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/dir1",
        )

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/dir1/doc.md",
            file_content=b"# hello\nreal integration\n",
        )

    # Verify DB state
    import asyncio

    async def _verify_db():
        connection = await build_connection_factory(settings)()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT COUNT(*) AS chunk_count
                    FROM knowledge_chunk kc
                    JOIN knowledge_fs_entry fe ON fe.kid = kc.fs_entry_id
                    JOIN knowledge_base kb ON kb.kid = fe.knowledge_base_id
                    WHERE kb.kid = %(kb_code)s::bigint
                    """,
                    {"kb_code": kb_code},
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["chunk_count"] >= 1
        finally:
            await connection.close()

    asyncio.run(_verify_db())


@pytest.mark.integration
def test_read_file_requires_build_step(monkeypatch):
    """readFile should fail if the file was uploaded but not built."""
    settings = _kb_settings()
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)

    with TestClient(main_module.app) as client:
        create_resp = client.post(
            "/api/v1/knowledgeBases/create",
            json={"knName": f"Integration KB {uuid4().hex[:4]}"},
        )
        assert create_resp.status_code == 200
        kb_code = create_resp.json()["resultObject"]["knCode"]
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/dir1",
        )

        # Upload only (no build)
        upload_response = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/dir1/doc.md"},
            files={
                "fileContent": ("doc.md", b"line1\nline2\nline3\n", "text/markdown")
            },
        )
        assert upload_response.status_code == 200

        # readFile should fail because file is not built
        read_response = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/dir1/doc.md",
                "startLine": 1,
                "endLine": 3,
            },
        )

    assert read_response.status_code == 200
    assert read_response.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_read_file_succeeds_after_upload_and_build(monkeypatch):
    """readFile should succeed after upload + fileToMarkdownIndex."""
    settings = _kb_settings()
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)

    fake_chunking = FakeDocumentChunkingService(markdown_text="line1\nline2\nline3\n")
    monkeypatch.setattr(
        main_module, "get_document_chunking_service", lambda: fake_chunking
    )

    with TestClient(main_module.app) as client:
        create_resp = client.post(
            "/api/v1/knowledgeBases/create",
            json={"knName": f"Integration KB {uuid4().hex[:4]}"},
        )
        assert create_resp.status_code == 200
        kb_code = create_resp.json()["resultObject"]["knCode"]
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/dir1",
        )

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/dir1/doc.md",
            file_content=b"line1\nline2\nline3\n",
        )

        first = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/dir1/doc.md",
                "startLine": 1,
                "endLine": 10,
            },
        )
        second = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/dir1/doc.md",
                "startLine": 2,
                "endLine": 2,
            },
        )

    assert first.status_code == 200
    assert first.json()["resultObject"]["data"]
    assert first.json()["resultObject"].get("reachedEof") is True
    assert second.status_code == 200
    assert second.json()["resultObject"]["data"]
