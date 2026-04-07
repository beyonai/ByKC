"""Real API integration tests for KB ingestion against openGauss and MinIO."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from minio import Minio

import by_qa.main as main_module
from by_qa.config import Settings
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.runtime import build_knowledge_base_service

DEFAULT_DSN = (
    "postgresql://gaussdb:OpenGauss%232026@127.0.0.1:15432/postgres?sslmode=disable"
)


def _kb_settings() -> Settings:
    return Settings(
        KB_OPENGAUSS_DSN=os.getenv("KB_OPENGAUSS_DSN", DEFAULT_DSN),
        KB_MINIO_ENDPOINT=os.getenv("KB_MINIO_ENDPOINT", "127.0.0.1:19000"),
        KB_MINIO_ACCESS_KEY=os.getenv("KB_MINIO_ACCESS_KEY", "minioadmin"),
        KB_MINIO_SECRET_KEY=os.getenv("KB_MINIO_SECRET_KEY", "minioadmin"),
        KB_MINIO_BUCKET=os.getenv("KB_MINIO_BUCKET", "knowledge-base"),
        KB_MINIO_MARKDOWN_BUCKET=os.getenv(
            "KB_MINIO_MARKDOWN_BUCKET", "knowledge-base-markdown"
        ),
        KB_MINIO_SECURE=False,
        EMBEDDING_MODEL_NAME=os.getenv("EMBEDDING_MODEL_NAME", "bge-m3"),
        EMBEDDING_BASE_URL="https://embedding.example.com",
        EMBEDDING_API_KEY="secret",
        EMBEDDING_DIMENSION=int(os.getenv("EMBEDDING_DIMENSION", "3")),
        EMBEDDING_DISTANCE_METRIC=os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine"),
    )


class CountingObjectStorage:
    """Wrapper that counts downloads while delegating to real object storage."""

    def __init__(self, inner):
        self.inner = inner
        self.bucket_name = inner.bucket_name
        self.markdown_bucket_name = inner.markdown_bucket_name
        self.download_calls: list[str] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def download_object(
        self, object_key: str, *, bucket_name: str | None = None
    ) -> bytes:
        self.download_calls.append(object_key)
        return self.inner.download_object(object_key, bucket_name=bucket_name)


@pytest.mark.integration
def test_kb_api_end_to_end_persists_to_opengauss_and_minio(monkeypatch):
    """Creating a KB and importing a document should persist DB and object storage state."""
    settings = _kb_settings()
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)

    kb_code = f"kb-{uuid4().hex[:8]}"
    item_code = f"doc-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        kb_response = client.post(
            "/api/v1/knowledge-bases/create",
            json={
                "kb_code": kb_code,
                "kb_name": "Integration KB",
                "status": "ACTIVE",
            },
        )
        assert kb_response.status_code == 200

        import_response = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": item_code,
                "file_path": f"dir1/{item_code}.md",
                "file_content": base64.b64encode(b"# hello\nreal integration\n").decode(
                    "ascii"
                ),
                "markdown_content": "# hello\nreal integration\n",
                "status": "ACTIVE",
                "source_code": "integration",
                "version": "v1",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 2,
                        "chunk_text": "hello real integration",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )
        assert import_response.status_code == 200

    connection = build_connection_factory(settings)()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    kb.kid AS knowledge_base_id,
                    ki.current_version_id,
                    kv.object_key,
                    kv.version,
                    c.chunk_text
                FROM knowledge_base kb
                JOIN knowledge_item ki ON ki.knowledge_base_id = kb.kid
                JOIN knowledge_fs_entry fs ON fs.kid = ki.fs_entry_id
                JOIN knowledge_item_version kv ON kv.kid = ki.current_version_id
                JOIN knowledge_item_chunk c ON c.knowledge_item_version_id = kv.kid
                WHERE kb.kb_code = %(kb_code)s
                  AND fs.full_path = %(item_code)s
                """,
                {"kb_code": kb_code, "item_code": f"dir1/{item_code}.md"},
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["version"] == "v1"
            assert row["chunk_text"] == "hello real integration"

            cursor.execute(
                """
                SELECT COUNT(*) AS projection_count
                FROM knowledge_item_chunk_retrieval_mv
                WHERE kb_code = %(kb_code)s
                  AND full_path = %(item_code)s
                """,
                {"kb_code": kb_code, "item_code": f"dir1/{item_code}.md"},
            )
            projection = cursor.fetchone()
            assert projection["projection_count"] == 1
    finally:
        connection.close()

    minio_client = Minio(
        endpoint=settings.kb_minio_endpoint,
        access_key=settings.kb_minio_access_key,
        secret_key=settings.kb_minio_secret_key,
        secure=settings.kb_minio_secure,
    )
    stat = minio_client.stat_object(settings.kb_minio_bucket, row["object_key"])
    assert stat.object_name == row["object_key"]


@pytest.mark.integration
def test_kb_api_rejects_invalid_embedding_without_persisting_document(monkeypatch):
    """Invalid embeddings should return 400 and leave no imported document rows."""
    settings = _kb_settings()
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)

    kb_code = f"kb-{uuid4().hex[:8]}"
    item_code = f"doc-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        kb_response = client.post(
            "/api/v1/knowledge-bases/create",
            json={
                "kb_code": kb_code,
                "kb_name": "Integration KB",
                "status": "ACTIVE",
            },
        )
        assert kb_response.status_code == 200

        import_response = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": item_code,
                "file_path": f"dir1/{item_code}.md",
                "file_content": base64.b64encode(b"# hello\nbad vector\n").decode(
                    "ascii"
                ),
                "markdown_content": "# hello\nbad vector\n",
                "status": "ACTIVE",
                "source_code": "integration",
                "version": "v1",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 2,
                        "chunk_text": "hello bad vector",
                        "embedding": [0.1, 0.2],
                    }
                ],
            },
        )
        assert import_response.status_code == 422

    connection = build_connection_factory(settings)()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS item_count
                FROM knowledge_base kb
                JOIN knowledge_item ki ON ki.knowledge_base_id = kb.kid
                JOIN knowledge_fs_entry fs ON fs.kid = ki.fs_entry_id
                WHERE kb.kb_code = %(kb_code)s
                  AND fs.full_path = %(item_code)s
                """,
                {"kb_code": kb_code, "item_code": f"dir1/{item_code}.md"},
            )
            row = cursor.fetchone()
            assert row["item_count"] == 0
    finally:
        connection.close()


@pytest.mark.integration
def test_fetch_api_caches_download_and_reuses_fresh_file(monkeypatch, tmp_path):
    """Fetch should write a cache file once and avoid repeated MinIO downloads while fresh."""
    settings = _kb_settings().model_copy(update={"agent_data_path": tmp_path})
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)

    kb_code = f"kb-{uuid4().hex[:8]}"
    item_code = f"doc-{uuid4().hex[:8]}"
    full_path = f"dir1/{item_code}.md"
    virtual_path = f"Integration KB/{full_path}"

    with TestClient(main_module.app) as client:
        assert (
            client.post(
                "/api/v1/knowledge-bases/create",
                json={
                    "kb_code": kb_code,
                    "kb_name": "Integration KB",
                    "status": "ACTIVE",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/knowledge-items/import",
                json={
                    "kb_code": kb_code,
                    "file_code": item_code,
                    "file_path": full_path,
                    "file_content": base64.b64encode(b"line1\nline2\nline3\n").decode(
                        "ascii"
                    ),
                    "markdown_content": "line1\nline2\nline3\n",
                    "status": "ACTIVE",
                    "source_code": "integration",
                    "version": "v1",
                    "chunks": [
                        {
                            "chunk_no": 1,
                            "start_line": 1,
                            "end_line": 3,
                            "chunk_text": "line1 line2 line3",
                            "embedding": [0.1, 0.2, 0.3],
                        }
                    ],
                },
            ).status_code
            == 200
        )

        service = build_knowledge_base_service(settings)
        service.object_storage = CountingObjectStorage(service.object_storage)
        monkeypatch.setattr(main_module, "_knowledge_base_service", service)

        first = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": virtual_path,
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 10,
            },
        )
        second = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": virtual_path,
                "content_type": "markdown",
                "start_line": 2,
                "end_line": 2,
            },
        )

    assert first.status_code == 200
    assert first.json()["data"]["data"] == "line1\nline2\nline3\n"
    assert first.json()["data"]["reached_eof"] is True
    assert second.status_code == 200
    assert second.json()["data"]["data"] == "line2\n"
    assert second.json()["data"]["reached_eof"] is False
    assert (
        service.object_storage.download_calls == [f"7/{full_path}/v1/{item_code}.md"]
        or len(service.object_storage.download_calls) == 1
    )
    cache_file = tmp_path / "kb_cache" / Path(virtual_path)
    assert cache_file.exists()
    assert cache_file.read_text(encoding="utf-8") == "line1\nline2\nline3\n"

    connection = build_connection_factory(settings)()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cache_status, virtual_path
                FROM knowledge_fetch_cache_index
                WHERE virtual_path = %(virtual_path)s
                """,
                {"virtual_path": virtual_path},
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["cache_status"] == "READY"
    finally:
        connection.close()


@pytest.mark.integration
def test_fetch_api_refreshes_expired_cache_and_rewrites_metadata(monkeypatch, tmp_path):
    """Expired cache files should be replaced by a fresh MinIO download on the next fetch."""
    settings = _kb_settings().model_copy(update={"agent_data_path": tmp_path})
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)

    kb_code = f"kb-{uuid4().hex[:8]}"
    item_code = f"doc-{uuid4().hex[:8]}"
    full_path = f"dir1/{item_code}.md"
    virtual_path = f"Integration KB/{full_path}"

    with TestClient(main_module.app) as client:
        assert (
            client.post(
                "/api/v1/knowledge-bases/create",
                json={
                    "kb_code": kb_code,
                    "kb_name": "Integration KB",
                    "status": "ACTIVE",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/knowledge-items/import",
                json={
                    "kb_code": kb_code,
                    "file_code": item_code,
                    "file_path": full_path,
                    "file_content": base64.b64encode(b"line1\nline2\nline3\n").decode(
                        "ascii"
                    ),
                    "markdown_content": "line1\nline2\nline3\n",
                    "status": "ACTIVE",
                    "source_code": "integration",
                    "version": "v1",
                    "chunks": [
                        {
                            "chunk_no": 1,
                            "start_line": 1,
                            "end_line": 3,
                            "chunk_text": "line1 line2 line3",
                            "embedding": [0.1, 0.2, 0.3],
                        }
                    ],
                },
            ).status_code
            == 200
        )

        service = build_knowledge_base_service(settings)
        service.object_storage = CountingObjectStorage(service.object_storage)
        monkeypatch.setattr(main_module, "_knowledge_base_service", service)

        cache_file = tmp_path / "kb_cache" / Path(virtual_path)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("stale\n", encoding="utf-8")
        connection = build_connection_factory(settings)()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE knowledge_fetch_cache_index
                    SET expires_at = NOW() - INTERVAL '1 day'
                    WHERE virtual_path = %(virtual_path)s
                    """,
                    {"virtual_path": virtual_path},
                )
            connection.commit()
        finally:
            connection.close()

        response = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": virtual_path,
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 3,
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["reached_eof"] is True
    assert (
        service.object_storage.download_calls == [f"7/{full_path}/v1/{item_code}.md"]
        or len(service.object_storage.download_calls) == 1
    )
    assert cache_file.read_text(encoding="utf-8") == "line1\nline2\nline3\n"

    connection = build_connection_factory(settings)()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cache_status, expires_at
                FROM knowledge_fetch_cache_index
                WHERE virtual_path = %(virtual_path)s
                """,
                {"virtual_path": virtual_path},
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["cache_status"] == "READY"
            assert row["expires_at"] is not None
    finally:
        connection.close()
