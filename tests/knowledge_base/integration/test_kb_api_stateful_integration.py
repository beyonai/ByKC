"""User-journey oriented stateful integration tests for knowledge_base APIs."""

from __future__ import annotations

import base64
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings
from by_qa.knowledge_base.infrastructure.runtime import (
    build_knowledge_item_search_service,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError

DEFAULT_DSN = (
    "postgresql://gaussdb:OpenGauss%232026@127.0.0.1:15432/postgres?sslmode=disable"
)


class FakeDocumentChunkingService:
    """Stable knowledge_build double used by cross-module API integration tests."""

    def __init__(self, *, markdown_text: str, embedding: list[float] | None = None):
        self.markdown_text = markdown_text
        self.embedding = embedding or [0.1, 0.2, 0.3]

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        assert isinstance(file_bytes, bytes)
        assert file_type == "pdf"
        return self.markdown_text

    def chunk_and_embed(self, file_bytes: bytes, *, filename: str) -> list[dict]:
        assert isinstance(filename, str)
        content = file_bytes.decode("utf-8")
        line_count = max(1, content.count("\n"))
        return [
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": line_count,
                "chunk_text": content.strip(),
                "embedding": self.embedding,
                "char_start": 0,
                "char_end": len(file_bytes),
            }
        ]


class FakeEmbeddingQueryService:
    """Deterministic embedding service used to keep search integration offline."""

    def __init__(self, embedding: list[float] | None = None):
        self.embedding = embedding or [0.1, 0.2, 0.3]

    def embed_query(self, query: str) -> list[float]:
        assert isinstance(query, str)
        return self.embedding


def _kb_settings(*, agent_data_path=None) -> Settings:
    updates = {
        "KB_OPENGAUSS_DSN": os.getenv("KB_OPENGAUSS_DSN", DEFAULT_DSN),
        "KB_MINIO_ENDPOINT": os.getenv("KB_MINIO_ENDPOINT", "127.0.0.1:19000"),
        "KB_MINIO_ACCESS_KEY": os.getenv("KB_MINIO_ACCESS_KEY", "minioadmin"),
        "KB_MINIO_SECRET_KEY": os.getenv("KB_MINIO_SECRET_KEY", "minioadmin"),
        "KB_MINIO_BUCKET": os.getenv("KB_MINIO_BUCKET", "knowledge-base"),
        "KB_MINIO_MARKDOWN_BUCKET": os.getenv(
            "KB_MINIO_MARKDOWN_BUCKET", "knowledge-base-markdown"
        ),
        "KB_MINIO_SECURE": False,
        "EMBEDDING_MODEL_NAME": os.getenv("EMBEDDING_MODEL_NAME", "bge-m3"),
        "EMBEDDING_BASE_URL": "https://embedding.example.com",
        "EMBEDDING_API_KEY": "secret",
        "EMBEDDING_DIMENSION": int(os.getenv("EMBEDDING_DIMENSION", "3")),
        "EMBEDDING_DISTANCE_METRIC": os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine"),
    }
    if agent_data_path is not None:
        updates["agent_data_path"] = agent_data_path
    return Settings(**updates)


def _reset_runtime(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)


def _set_document_chunking_service(
    monkeypatch: pytest.MonkeyPatch,
    service: FakeDocumentChunkingService,
) -> None:
    monkeypatch.setattr(main_module, "get_document_chunking_service", lambda: service)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)


def _set_search_service(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    embedding: list[float] | None = None,
) -> None:
    service = build_knowledge_item_search_service(settings)
    service.embedding_query_service = FakeEmbeddingQueryService(embedding)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", service)


def _disable_kb_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable startup/shutdown runtime initialization for route-level failure tests."""
    monkeypatch.setattr(
        main_module, "_initialize_knowledge_base_runtime", lambda enabled_modules: None
    )
    monkeypatch.setattr(
        main_module, "_shutdown_knowledge_base_runtime", lambda enabled_modules: None
    )


def _create_kb(client: TestClient, kb_code: str, kb_name: str) -> None:
    response = client.post(
        "/api/v1/knowledge-bases/create",
        json={"kb_code": kb_code, "kb_name": kb_name, "status": "ACTIVE"},
    )
    assert response.status_code == 200, response.text


def _create_directory(
    client: TestClient,
    *,
    kb_code: str,
    directory_code: str,
    directory_path: str,
) -> None:
    response = client.post(
        "/api/v1/directories/create",
        json={
            "kb_code": kb_code,
            "directory_code": directory_code,
            "directory_path": directory_path,
            "directory_description": f"{directory_code} description",
            "source_code": "integration",
            "status": "ACTIVE",
        },
    )
    assert response.status_code == 200, response.text


def _import_markdown_file(
    client: TestClient,
    *,
    kb_code: str,
    file_code: str,
    file_path: str,
    markdown_content: str,
    version: str = "v1",
) -> None:
    response = client.post(
        "/api/v1/knowledge-items/import",
        json={
            "kb_code": kb_code,
            "file_code": file_code,
            "file_path": file_path,
            "file_description": f"{file_code} description",
            "file_content": base64.b64encode(markdown_content.encode("utf-8")).decode(
                "ascii"
            ),
            "version": version,
            "source_code": "integration",
            "status": "ACTIVE",
            "markdown_content": markdown_content,
            "chunks": [
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": max(1, markdown_content.count("\n")),
                    "chunk_text": markdown_content.strip(),
                    "embedding": [0.1, 0.2, 0.3],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text


def _build_markdown_via_api(
    client: TestClient,
    *,
    original_bytes: bytes,
    file_type: str = "pdf",
) -> str:
    response = client.post(
        "/api/v1/file-to-markdown",
        json={
            "content": base64.b64encode(original_bytes).decode("ascii"),
            "type": file_type,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["md_content"]


def _build_chunks_via_api(client: TestClient, *, markdown_content: str) -> list[dict]:
    response = client.post(
        "/api/v1/build-markdown-index",
        json={"content": markdown_content},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["chunks"]


@pytest.mark.integration
def test_create_directory_returns_success_then_duplicate_path_conflict(monkeypatch):
    """Directory admin can create a folder once and gets a conflict on duplicate path reuse."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)

        first = client.post(
            "/api/v1/directories/create",
            json={
                "kb_code": kb_code,
                "directory_code": f"dir-{uuid4().hex[:6]}",
                "directory_path": "/Policies",
                "directory_description": "Policies",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )
        duplicate_path = client.post(
            "/api/v1/directories/create",
            json={
                "kb_code": kb_code,
                "directory_code": f"dir-{uuid4().hex[:6]}",
                "directory_path": "/Policies",
                "directory_description": "Policies duplicate",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )

    assert first.status_code == 200
    assert duplicate_path.status_code == 409
    assert duplicate_path.json()["error"]["error_code"] == "KB_DIRECTORY_PATH_CONFLICT"


@pytest.mark.integration
def test_create_empty_knowledge_base_exposes_root_and_rejects_duplicate_code(
    monkeypatch,
):
    """Creating an empty KB should expose its root entry and reject duplicate kb_code reuse."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        first = client.post(
            "/api/v1/knowledge-bases/create",
            json={"kb_code": kb_code, "kb_name": kb_name, "status": "ACTIVE"},
        )
        root = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": "/"},
        )
        duplicate = client.post(
            "/api/v1/knowledge-bases/create",
            json={"kb_code": kb_code, "kb_name": kb_name, "status": "ACTIVE"},
        )

    assert first.status_code == 200
    assert root.status_code == 200
    assert root.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}",
            "type": "directory",
            "size": 0,
        }
    ]
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["error_code"] == "KB_CODE_CONFLICT"


@pytest.mark.integration
def test_create_directory_requires_existing_parent_and_exposes_new_child_at_parent_level(
    monkeypatch,
):
    """Single-level directory creation should fail on missing parent and succeed into parent listing."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        missing_parent = client.post(
            "/api/v1/directories/create",
            json={
                "kb_code": kb_code,
                "directory_code": f"dir-{uuid4().hex[:6]}",
                "directory_path": "/Missing/Leaf",
                "directory_description": "missing parent",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )
        create_root_child = client.post(
            "/api/v1/directories/create",
            json={
                "kb_code": kb_code,
                "directory_code": f"dir-{uuid4().hex[:6]}",
                "directory_path": "/Policies",
                "directory_description": "policies",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )
        kb_root = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": kb_name},
        )

    assert missing_parent.status_code == 404
    assert (
        missing_parent.json()["error"]["error_code"] == "KB_DIRECTORY_PARENT_NOT_FOUND"
    )
    assert create_root_child.status_code == 200
    assert kb_root.status_code == 200
    assert kb_root.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies",
            "type": "directory",
            "size": 0,
        }
    ]


@pytest.mark.integration
def test_write_file_and_write_index_make_markdown_and_original_readable(
    monkeypatch, tmp_path
):
    """Content admin can upload first, index later, then read both original and markdown."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    file_code = f"file-{uuid4().hex[:8]}"
    file_path = "Policies/manual.md"
    virtual_path = f"{kb_name}/{file_path}"
    markdown_content = "line1\nline2\nline3\n"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )

        write_file = client.post(
            "/api/v1/write-file",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "file_path": file_path,
                "file_description": "policy file",
                "file_content": base64.b64encode(
                    markdown_content.encode("utf-8")
                ).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )
        assert write_file.status_code == 200, write_file.text

        write_index = client.post(
            "/api/v1/write-index",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "version": "v1",
                "markdown_content": markdown_content,
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 3,
                        "chunk_text": "line1\nline2\nline3",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )
        assert write_index.status_code == 200, write_index.text

        markdown_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": virtual_path,
                "content_type": "markdown",
                "start_line": 2,
                "end_line": 3,
            },
        )
        original_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": virtual_path,
                "content_type": "original",
            },
        )

    assert markdown_read.status_code == 200
    assert markdown_read.json()["data"]["data"] == "line2\nline3\n"
    assert markdown_read.json()["data"]["content_type"] == "markdown"
    assert original_read.status_code == 200
    assert original_read.json()["data"]["content_type"] == "original"
    assert original_read.json()["data"]["url"]


@pytest.mark.integration
def test_write_index_returns_not_found_when_file_does_not_exist(monkeypatch):
    """Indexing a nonexistent file should fail cleanly for the content admin."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        response = client.post(
            "/api/v1/write-index",
            json={
                "kb_code": kb_code,
                "file_code": "missing-file",
                "version": "v1",
                "markdown_content": "# missing",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "# missing",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == "KB_FILE_NOT_FOUND"


@pytest.mark.integration
def test_directory_rename_updates_parent_and_child_queries(monkeypatch, tmp_path):
    """Renaming a directory should update browse, match, and read behavior together."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    parent_code = f"dir-{uuid4().hex[:6]}"
    child_code = f"dir-{uuid4().hex[:6]}"
    file_code = f"file-{uuid4().hex[:8]}"
    markdown_content = "alpha\nbeta\ngamma\n"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=parent_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=child_code,
            directory_path="/Policies/2024",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=file_code,
            file_path="Policies/2024/handbook.md",
            markdown_content=markdown_content,
        )

        before_parent = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        before_child = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024"},
        )

        rename = client.post(
            "/api/v1/directories/update",
            json={
                "kb_code": kb_code,
                "directory_code": child_code,
                "directory_name": "Archive",
                "directory_description": "renamed archive",
            },
        )
        assert rename.status_code == 200, rename.text

        after_parent = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        old_child = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024"},
        )
        new_child = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive"},
        )
        old_glob = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024/*.md"},
        )
        new_glob = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive/*.md"},
        )
        old_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2024/handbook.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 2,
            },
        )
        new_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/Archive/handbook.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 2,
            },
        )

    assert before_parent.status_code == 200
    assert before_child.status_code == 200
    assert before_parent.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024",
            "type": "directory",
            "size": 0,
        }
    ]
    assert before_child.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024/handbook.md",
            "type": "file",
            "size": len(markdown_content.encode("utf-8")),
        }
    ]
    assert after_parent.status_code == 200
    assert after_parent.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/Archive",
            "type": "directory",
            "size": 0,
        }
    ]
    assert old_child.status_code == 404
    assert old_child.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert new_child.status_code == 200
    assert new_child.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/Archive/handbook.md",
            "type": "file",
            "size": len(markdown_content.encode("utf-8")),
        }
    ]
    assert old_glob.status_code == 200
    assert old_glob.json()["data"] == []
    assert new_glob.status_code == 200
    assert new_glob.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/Archive/handbook.md",
            "type": "file",
            "size": len(markdown_content.encode("utf-8")),
        }
    ]
    assert old_read.status_code == 404
    assert old_read.json()["error"]["error_code"] == "KB_FILE_NOT_FOUND"
    assert new_read.status_code == 200
    assert new_read.json()["data"]["data"] == "alpha\nbeta\n"


@pytest.mark.integration
def test_directory_delete_removes_subtree_from_follow_up_queries(monkeypatch, tmp_path):
    """Deleting a non-empty directory should remove the subtree from all follow-up reads."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    parent_code = f"dir-{uuid4().hex[:6]}"
    child_code = f"dir-{uuid4().hex[:6]}"
    file_code = f"file-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=parent_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=child_code,
            directory_path="/Policies/Archive",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=file_code,
            file_path="Policies/Archive/handbook.md",
            markdown_content="line1\nline2\n",
        )

        delete_response = client.post(
            "/api/v1/directories/delete",
            json={"kb_code": kb_code, "directory_code": child_code},
        )
        parent_list = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        deleted_list = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive"},
        )
        deleted_glob = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive/*.md"},
        )
        deleted_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/Archive/handbook.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )

    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["is_deleted"] is True
    assert parent_list.status_code == 200
    assert parent_list.json()["data"] == []
    assert deleted_list.status_code == 404
    assert deleted_list.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert deleted_glob.status_code == 200
    assert deleted_glob.json()["data"] == []
    assert deleted_read.status_code == 404
    assert deleted_read.json()["error"]["error_code"] == "KB_FILE_NOT_FOUND"


@pytest.mark.integration
def test_build_outputs_can_be_imported_into_a_multilevel_directory_tree(
    monkeypatch, tmp_path
):
    """Content admin can build markdown/chunks first, then import into a deep directory tree."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Handbook\n\nalpha\nbeta\ngamma\n"),
    )

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    file_code = f"file-{uuid4().hex[:8]}"
    original_bytes = b"%PDF-1.4 fake handbook bytes"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/2024/Q1",
        )

        markdown_content = _build_markdown_via_api(
            client,
            original_bytes=original_bytes,
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)

        import_response = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "file_path": "Policies/2024/Q1/handbook.pdf",
                "file_description": "deep handbook",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )
        root_children = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": kb_name},
        )
        level_one = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        level_two = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024"},
        )
        level_three = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024/Q1"},
        )
        markdown_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2024/Q1/handbook.pdf",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 3,
            },
        )
        original_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2024/Q1/handbook.pdf",
                "content_type": "original",
            },
        )

    assert import_response.status_code == 200, import_response.text
    assert root_children.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies",
            "type": "directory",
            "size": 0,
        }
    ]
    assert level_one.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024",
            "type": "directory",
            "size": 0,
        }
    ]
    assert level_two.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024/Q1",
            "type": "directory",
            "size": 0,
        }
    ]
    assert level_three.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024/Q1/handbook.pdf",
            "type": "file",
            "size": len(original_bytes),
        }
    ]
    assert markdown_read.status_code == 200
    assert markdown_read.json()["data"]["data"] == "# Handbook\n\nalpha\n"
    assert original_read.status_code == 200
    assert original_read.json()["data"]["url"]


@pytest.mark.integration
def test_multilevel_directory_tree_lists_direct_children_and_supports_glob_matching(
    monkeypatch, tmp_path
):
    """Multi-level trees should preserve direct-child listings and pattern-based matches."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/A",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/A/B",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/A/B/C",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="A/B/C/one.md",
            markdown_content="one\n",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="A/B/C/two.md",
            markdown_content="two\n",
        )

        root_list = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/A"},
        )
        middle_list = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/A/B"},
        )
        file_path_list = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/A/B/C/one.md"},
        )
        glob_list = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/A/B/C/*.md"},
        )

    assert root_list.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/A/B",
            "type": "directory",
            "size": 0,
        }
    ]
    assert middle_list.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/A/B/C",
            "type": "directory",
            "size": 0,
        }
    ]
    assert file_path_list.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/A/B/C/one.md",
            "type": "file",
            "size": len("one\n".encode("utf-8")),
        }
    ]
    assert sorted(glob_list.json()["data"], key=lambda item: item["name"]) == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/A/B/C/one.md",
            "type": "file",
            "size": len("one\n".encode("utf-8")),
        },
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/A/B/C/two.md",
            "type": "file",
            "size": len("two\n".encode("utf-8")),
        },
    ]


@pytest.mark.integration
def test_build_outputs_can_drive_stepwise_write_into_a_multilevel_directory_tree(
    monkeypatch, tmp_path
):
    """Content admin can use knowledge_build output to complete write-file plus write-index."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Policy\n\nline1\nline2\nline3\n"),
    )

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    file_code = f"file-{uuid4().hex[:8]}"
    original_bytes = b"%PDF-1.4 fake policy bytes"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/2024/Q2",
        )

        markdown_content = _build_markdown_via_api(
            client,
            original_bytes=original_bytes,
        )
        write_file = client.post(
            "/api/v1/write-file",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "file_path": "Policies/2024/Q2/policy.pdf",
                "file_description": "stepwise policy",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)
        write_index = client.post(
            "/api/v1/write-index",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "version": "v1",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )
        glob_response = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024/Q2/*.pdf"},
        )
        markdown_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2024/Q2/policy.pdf",
                "content_type": "markdown",
                "start_line": 2,
                "end_line": 4,
            },
        )
        original_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2024/Q2/policy.pdf",
                "content_type": "original",
            },
        )

    assert write_file.status_code == 200, write_file.text
    assert write_index.status_code == 200, write_index.text
    assert glob_response.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024/Q2/policy.pdf",
            "type": "file",
            "size": len(original_bytes),
        }
    ]
    assert markdown_read.status_code == 200
    assert markdown_read.json()["data"]["data"] == "\nline1\nline2\n"
    assert original_read.status_code == 200
    assert original_read.json()["data"]["url"]


@pytest.mark.integration
def test_renaming_a_middle_directory_updates_all_descendant_paths(
    monkeypatch, tmp_path
):
    """Renaming a middle directory should move descendant directories and files together."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    top_code = f"dir-{uuid4().hex[:6]}"
    middle_code = f"dir-{uuid4().hex[:6]}"
    leaf_code = f"dir-{uuid4().hex[:6]}"
    file_code = f"file-{uuid4().hex[:8]}"
    markdown_content = "line1\nline2\nline3\n"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=top_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=middle_code,
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=leaf_code,
            directory_path="/Policies/2024/Q1",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=file_code,
            file_path="Policies/2024/Q1/handbook.md",
            markdown_content=markdown_content,
        )

        rename = client.post(
            "/api/v1/directories/update",
            json={
                "kb_code": kb_code,
                "directory_code": middle_code,
                "directory_name": "2025",
            },
        )
        top_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        middle_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2025"},
        )
        leaf_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2025/Q1"},
        )
        old_leaf = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024/Q1"},
        )
        old_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2024/Q1/handbook.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )
        new_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2025/Q1/handbook.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 2,
            },
        )
        old_glob = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024/**/*.md"},
        )
        new_glob = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2025/Q1/*.md"},
        )

    assert rename.status_code == 200, rename.text
    assert top_after.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2025",
            "type": "directory",
            "size": 0,
        }
    ]
    assert middle_after.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2025/Q1",
            "type": "directory",
            "size": 0,
        }
    ]
    assert leaf_after.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2025/Q1/handbook.md",
            "type": "file",
            "size": len(markdown_content.encode("utf-8")),
        }
    ]
    assert old_leaf.status_code == 404
    assert old_leaf.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert old_read.status_code == 404
    assert new_read.status_code == 200
    assert new_read.json()["data"]["data"] == "line1\nline2\n"
    assert old_glob.status_code == 200
    assert old_glob.json()["data"] == []
    assert new_glob.status_code == 200
    assert new_glob.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2025/Q1/handbook.md",
            "type": "file",
            "size": len(markdown_content.encode("utf-8")),
        }
    ]


@pytest.mark.integration
def test_deleting_a_middle_directory_removes_the_entire_descendant_subtree(
    monkeypatch, tmp_path
):
    """Deleting a middle directory should remove every descendant directory and file."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    top_code = f"dir-{uuid4().hex[:6]}"
    middle_code = f"dir-{uuid4().hex[:6]}"
    leaf_one_code = f"dir-{uuid4().hex[:6]}"
    leaf_two_code = f"dir-{uuid4().hex[:6]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=top_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=middle_code,
            directory_path="/Policies/Archive",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=leaf_one_code,
            directory_path="/Policies/Archive/Q1",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=leaf_two_code,
            directory_path="/Policies/Archive/Q2",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/Archive/Q1/a.md",
            markdown_content="q1-line1\nq1-line2\n",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/Archive/Q2/b.md",
            markdown_content="q2-line1\nq2-line2\n",
        )

        delete_response = client.post(
            "/api/v1/directories/delete",
            json={"kb_code": kb_code, "directory_code": middle_code},
        )
        top_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        deleted_middle = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive"},
        )
        deleted_leaf_one = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive/Q1"},
        )
        deleted_leaf_two = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive/Q2"},
        )
        deleted_glob = client.post(
            "/api/v1/glob",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/Archive/*/*.md"},
        )
        deleted_read_one = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/Archive/Q1/a.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )
        deleted_read_two = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/Archive/Q2/b.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )

    assert delete_response.status_code == 200
    assert top_after.json()["data"] == []
    assert deleted_middle.status_code == 404
    assert deleted_middle.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert deleted_leaf_one.status_code == 404
    assert deleted_leaf_one.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert deleted_leaf_two.status_code == 404
    assert deleted_leaf_two.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert deleted_glob.status_code == 200
    assert deleted_glob.json()["data"] == []
    assert deleted_read_one.status_code == 404
    assert deleted_read_two.status_code == 404


@pytest.mark.integration
def test_updating_kb_name_renames_the_root_path_for_follow_up_queries(
    monkeypatch, tmp_path
):
    """Knowledge-base rename should update root-level browse and follow-up read paths."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    old_name = f"Integration KB {uuid4().hex[:4]}"
    new_name = f"Renamed KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, old_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/guide.md",
            markdown_content="guide-line1\nguide-line2\n",
        )

        root_before = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": "/"},
        )
        update_response = client.post(
            "/api/v1/knowledge-bases/update",
            json={"kb_code": kb_code, "kb_name": new_name},
        )
        root_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": "/"},
        )
        old_root_path = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": old_name},
        )
        new_root_path = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": new_name},
        )
        old_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{old_name}/Policies/guide.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )
        new_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{new_name}/Policies/guide.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 2,
            },
        )

    assert root_before.status_code == 200
    assert {
        "kb_code": kb_code,
        "name": f"/{old_name}",
        "type": "directory",
        "size": 0,
    } in root_before.json()["data"]
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["data"]["kb_name"] == new_name
    assert root_after.status_code == 200
    assert {
        "kb_code": kb_code,
        "name": f"/{new_name}",
        "type": "directory",
        "size": 0,
    } in root_after.json()["data"]
    assert all(item["name"] != f"/{old_name}" for item in root_after.json()["data"])
    assert old_root_path.status_code == 404
    assert old_root_path.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert new_root_path.status_code == 200
    assert new_root_path.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{new_name}/Policies",
            "type": "directory",
            "size": 0,
        }
    ]
    assert old_read.status_code == 404
    assert new_read.status_code == 200
    assert new_read.json()["data"]["data"] == "guide-line1\nguide-line2\n"


@pytest.mark.integration
def test_atomic_import_and_stepwise_write_have_the_same_external_read_behavior(
    monkeypatch, tmp_path
):
    """Atomic import and stepwise write should expose equivalent browse and read behavior."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(
            markdown_text="# Shared\n\nsame-line1\nsame-line2\n"
        ),
    )

    atomic_kb_code = f"kb-{uuid4().hex[:8]}"
    atomic_kb_name = f"Atomic KB {uuid4().hex[:4]}"
    step_kb_code = f"kb-{uuid4().hex[:8]}"
    step_kb_name = f"Step KB {uuid4().hex[:4]}"
    original_bytes = b"%PDF-1.4 same content"

    with TestClient(main_module.app) as client:
        markdown_content = _build_markdown_via_api(
            client, original_bytes=original_bytes
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)

        _create_kb(client, atomic_kb_code, atomic_kb_name)
        _create_directory(
            client,
            kb_code=atomic_kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        atomic_import = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": atomic_kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/shared.pdf",
                "file_description": "atomic file",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )

        _create_kb(client, step_kb_code, step_kb_name)
        _create_directory(
            client,
            kb_code=step_kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        step_write_file = client.post(
            "/api/v1/write-file",
            json={
                "kb_code": step_kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/shared.pdf",
                "file_description": "step file",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )
        step_write_index = client.post(
            "/api/v1/write-index",
            json={
                "kb_code": step_kb_code,
                "file_code": step_write_file.json()["data"]["file_code"],
                "version": "v1",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )

        atomic_list = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [atomic_kb_code], "path": f"{atomic_kb_name}/Policies"},
        )
        step_list = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [step_kb_code], "path": f"{step_kb_name}/Policies"},
        )
        atomic_markdown = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [atomic_kb_code],
                "path": f"{atomic_kb_name}/Policies/shared.pdf",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 3,
            },
        )
        step_markdown = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [step_kb_code],
                "path": f"{step_kb_name}/Policies/shared.pdf",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 3,
            },
        )
        atomic_original = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [atomic_kb_code],
                "path": f"{atomic_kb_name}/Policies/shared.pdf",
                "content_type": "original",
            },
        )
        step_original = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [step_kb_code],
                "path": f"{step_kb_name}/Policies/shared.pdf",
                "content_type": "original",
            },
        )

    assert atomic_import.status_code == 200, atomic_import.text
    assert step_write_file.status_code == 200, step_write_file.text
    assert step_write_index.status_code == 200, step_write_index.text
    assert atomic_list.json()["data"] == [
        {
            "kb_code": atomic_kb_code,
            "name": f"/{atomic_kb_name}/Policies/shared.pdf",
            "type": "file",
            "size": len(original_bytes),
        }
    ]
    assert step_list.json()["data"] == [
        {
            "kb_code": step_kb_code,
            "name": f"/{step_kb_name}/Policies/shared.pdf",
            "type": "file",
            "size": len(original_bytes),
        }
    ]
    assert atomic_markdown.status_code == 200
    assert step_markdown.status_code == 200
    assert (
        atomic_markdown.json()["data"]["data"] == step_markdown.json()["data"]["data"]
    )
    assert atomic_markdown.json()["data"]["data"] == "# Shared\n\nsame-line1\n"
    assert atomic_original.status_code == 200
    assert step_original.status_code == 200
    assert atomic_original.json()["data"]["content_type"] == "original"
    assert step_original.json()["data"]["content_type"] == "original"


@pytest.mark.integration
def test_deleting_a_single_file_removes_it_from_follow_up_browse_and_read(
    monkeypatch, tmp_path
):
    """Deleting one file should remove only that file while preserving sibling visibility."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    kept_file_code = f"file-{uuid4().hex[:8]}"
    deleted_file_code = f"file-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=kept_file_code,
            file_path="Policies/keep.md",
            markdown_content="keep-line1\nkeep-line2\n",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=deleted_file_code,
            file_path="Policies/delete.md",
            markdown_content="delete-line1\ndelete-line2\n",
        )

        delete_response = client.post(
            "/api/v1/knowledge-items/delete",
            json={"kb_code": kb_code, "file_code": deleted_file_code},
        )
        list_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        deleted_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/delete.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )
        kept_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/keep.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 2,
            },
        )

    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["is_deleted"] is True
    assert list_after.status_code == 200
    assert list_after.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/keep.md",
            "type": "file",
            "size": len("keep-line1\nkeep-line2\n".encode("utf-8")),
        }
    ]
    assert deleted_read.status_code == 404
    assert kept_read.status_code == 200
    assert kept_read.json()["data"]["data"] == "keep-line1\nkeep-line2\n"


@pytest.mark.integration
def test_read_file_rejects_invalid_markdown_line_windows(monkeypatch, tmp_path):
    """Reader should get stable validation errors for invalid markdown line windows."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/window.md",
            markdown_content="w1\nw2\nw3\n",
        )

        zero_start = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/window.md",
                "content_type": "markdown",
                "start_line": 0,
                "end_line": 1,
            },
        )
        reversed_window = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/window.md",
                "content_type": "markdown",
                "start_line": 3,
                "end_line": 2,
            },
        )

    assert zero_start.status_code == 422
    assert zero_start.json()["error"]["error_code"] == "KB_READ_FILE_INVALID"
    assert reversed_window.status_code == 422
    assert reversed_window.json()["error"]["error_code"] == "KB_READ_FILE_INVALID"


@pytest.mark.integration
def test_download_file_returns_original_bytes_with_non_ascii_filename(
    monkeypatch, tmp_path
):
    """Download-file should return original bytes and a safe header for non-ASCII names."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = "DEMO知识库"
    file_name = "开源项目最佳实践汇报.md"
    file_path = f"考勤制度/{file_name}"
    virtual_path = f"{kb_name}/{file_path}"
    original_content = "# 最佳实践\n\n第一条：保持接口清晰。\n"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/考勤制度",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path=file_path,
            markdown_content=original_content,
        )

        response = client.post(
            "/api/v1/download-file",
            json={
                "kb_codes": [kb_code],
                "path": virtual_path,
            },
        )

    assert response.status_code == 200
    assert response.content == original_content.encode("utf-8")
    assert response.headers["content-type"].startswith("text/markdown")
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="download.md"; '
        "filename*=UTF-8''%E5%BC%80%E6%BA%90%E9%A1%B9%E7%9B%AE%E6%9C%80%E4%BD%B3%E5%AE%9E%E8%B7%B5%E6%B1%87%E6%8A%A5.md"
    )


@pytest.mark.integration
def test_download_file_returns_binary_pdf_bytes(monkeypatch, tmp_path):
    """Download-file should return original binary bytes and PDF headers."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# PDF\n\nbinary content\n"),
    )

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    original_bytes = b"%PDF-1.4 binary handbook bytes"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        markdown_content = _build_markdown_via_api(
            client, original_bytes=original_bytes
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)
        import_response = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/handbook.pdf",
                "file_description": "binary handbook",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )
        response = client.post(
            "/api/v1/download-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/handbook.pdf",
            },
        )

    assert import_response.status_code == 200, import_response.text
    assert response.status_code == 200
    assert response.content == original_bytes
    assert response.headers["content-type"].startswith("application/pdf")
    assert (
        response.headers["content-disposition"] == 'attachment; filename="handbook.pdf"'
    )


@pytest.mark.integration
def test_search_returns_hits_for_content_imported_from_real_build_outputs(
    monkeypatch, tmp_path
):
    """Search user should hit content that was built through knowledge_build and then imported."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(
            markdown_text="# FAQ\n\nvacation policy carryover\n"
        ),
    )
    _set_search_service(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    original_bytes = b"%PDF-1.4 faq content"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        markdown_content = _build_markdown_via_api(
            client, original_bytes=original_bytes
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)
        import_response = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/faq.pdf",
                "file_description": "faq file",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "hr",
                "status": "ACTIVE",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )
        search_response = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "vacation carryover", "kb_codes": [kb_code], "top_k": 5},
        )

    assert import_response.status_code == 200, import_response.text
    assert search_response.status_code == 200, search_response.text
    body = search_response.json()["data"]
    assert body["meta"]["returned_count"] >= 1
    assert body["items"][0]["kb_code"] == kb_code
    assert body["items"][0]["file_path"] == "Policies/faq.pdf"
    assert "vacation policy carryover" in body["items"][0]["chunk_text"]


@pytest.mark.integration
def test_search_respects_source_and_type_filters(monkeypatch, tmp_path):
    """Search filters should keep only results matching the requested source/type constraints."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_search_service(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/hr.md",
            markdown_content="annual leave handbook\n",
        )
        other = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/finance.txt",
                "file_description": "finance file",
                "file_content": base64.b64encode(b"annual leave handbook\n").decode(
                    "ascii"
                ),
                "version": "v1",
                "source_code": "finance",
                "status": "ACTIVE",
                "markdown_content": "annual leave handbook\n",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "annual leave handbook",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )
        filtered = client.post(
            "/api/v1/knowledge-items/search",
            json={
                "query": "annual leave handbook",
                "kb_codes": [kb_code],
                "source_codes": ["integration"],
                "type_codes": ["md"],
                "top_k": 10,
            },
        )

    assert other.status_code == 200, other.text
    assert filtered.status_code == 200, filtered.text
    items = filtered.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["source_code"] == "integration"
    assert items[0]["type_code"] == "md"
    assert items[0]["file_path"] == "Policies/hr.md"


@pytest.mark.integration
def test_search_path_updates_after_middle_directory_rename(monkeypatch, tmp_path):
    """Search results should follow the new file path after a middle directory rename."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_search_service(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    middle_code = f"dir-{uuid4().hex[:6]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=middle_code,
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/2024/Q1",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/2024/Q1/rename-search.md",
            markdown_content="rename target sentence\n",
        )
        before = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "rename target", "kb_codes": [kb_code]},
        )
        rename = client.post(
            "/api/v1/directories/update",
            json={
                "kb_code": kb_code,
                "directory_code": middle_code,
                "directory_name": "2025",
            },
        )
        after = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "rename target", "kb_codes": [kb_code]},
        )

    assert before.status_code == 200
    assert (
        before.json()["data"]["items"][0]["file_path"]
        == "Policies/2024/Q1/rename-search.md"
    )
    assert rename.status_code == 200, rename.text
    assert after.status_code == 200
    assert (
        after.json()["data"]["items"][0]["file_path"]
        == "Policies/2025/Q1/rename-search.md"
    )


@pytest.mark.integration
def test_search_results_disappear_after_single_file_delete(monkeypatch, tmp_path):
    """Deleting a file should remove its chunks from later search results."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_search_service(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    file_code = f"file-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=file_code,
            file_path="Policies/delete-search.md",
            markdown_content="search should disappear\n",
        )
        before = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "disappear", "kb_codes": [kb_code]},
        )
        delete_response = client.post(
            "/api/v1/knowledge-items/delete",
            json={"kb_code": kb_code, "file_code": file_code},
        )
        after = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "disappear", "kb_codes": [kb_code]},
        )

    assert before.status_code == 200
    assert before.json()["data"]["meta"]["returned_count"] >= 1
    assert delete_response.status_code == 200
    assert after.status_code == 200
    assert after.json()["data"]["items"] == []
    assert after.json()["data"]["meta"]["returned_count"] == 0


@pytest.mark.integration
def test_search_results_disappear_after_middle_directory_delete(monkeypatch, tmp_path):
    """Deleting a middle directory should remove descendant file hits from search results."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_search_service(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    middle_code = f"dir-{uuid4().hex[:6]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=middle_code,
            directory_path="/Policies/Archive",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/Archive/Q1",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/Archive/Q1/dir-delete-search.md",
            markdown_content="subtree search disappears\n",
        )
        before = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "subtree disappears", "kb_codes": [kb_code]},
        )
        delete_response = client.post(
            "/api/v1/directories/delete",
            json={"kb_code": kb_code, "directory_code": middle_code},
        )
        after = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "subtree disappears", "kb_codes": [kb_code]},
        )

    assert before.status_code == 200
    assert before.json()["data"]["meta"]["returned_count"] >= 1
    assert delete_response.status_code == 200
    assert after.status_code == 200
    assert after.json()["data"]["items"] == []


@pytest.mark.integration
def test_failed_import_after_build_output_leaves_no_visible_file(monkeypatch, tmp_path):
    """If import fails after successful build output generation, no file should become visible."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Broken\n\nshould not persist\n"),
    )

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    original_bytes = b"%PDF-1.4 broken import"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        markdown_content = _build_markdown_via_api(
            client, original_bytes=original_bytes
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)
        chunks[0]["embedding"] = [0.1, 0.2]

        import_response = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/broken.pdf",
                "file_description": "broken file",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )
        list_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        read_after = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/broken.pdf",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )

    assert import_response.status_code == 422
    assert list_after.status_code == 200
    assert list_after.json()["data"] == []
    assert read_after.status_code == 404


@pytest.mark.integration
def test_failed_write_index_leaves_original_readable_but_markdown_index_unavailable(
    monkeypatch, tmp_path
):
    """If write-index fails, the original file should remain readable without a markdown sidecar."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Step\n\npartial failure case\n"),
    )

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    file_code = f"file-{uuid4().hex[:8]}"
    original_bytes = b"%PDF-1.4 partial failure"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        markdown_content = _build_markdown_via_api(
            client, original_bytes=original_bytes
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)
        write_file = client.post(
            "/api/v1/write-file",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "file_path": "Policies/partial.pdf",
                "file_description": "partial file",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )
        chunks[0]["embedding"] = [0.1, 0.2]
        write_index = client.post(
            "/api/v1/write-index",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "version": "v1",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )
        original_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/partial.pdf",
                "content_type": "original",
            },
        )
        markdown_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/partial.pdf",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )

    assert write_file.status_code == 200, write_file.text
    assert write_index.status_code == 422
    assert original_read.status_code == 200
    assert original_read.json()["data"]["content_type"] == "original"
    assert markdown_read.status_code == 200
    assert markdown_read.json()["data"]["content_type"] == "original"
    assert markdown_read.json()["data"]["url"]


@pytest.mark.integration
def test_deleting_a_knowledge_base_removes_root_visibility_readability_and_search(
    monkeypatch, tmp_path
):
    """Deleting a knowledge base should hide it from root browse, reads, and search."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_search_service(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/base-delete.md",
            markdown_content="knowledge base removal search\n",
        )

        root_before = client.post(
            "/api/v1/list_dir", json={"kb_codes": [kb_code], "path": "/"}
        )
        search_before = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "removal search", "kb_codes": [kb_code]},
        )
        delete_response = client.post(
            "/api/v1/knowledge-bases/delete",
            json={"kb_code": kb_code},
        )
        root_after = client.post(
            "/api/v1/list_dir", json={"kb_codes": [kb_code], "path": "/"}
        )
        read_after = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/base-delete.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )
        search_after = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "removal search", "kb_codes": [kb_code]},
        )

    assert root_before.status_code == 200
    assert {
        "kb_code": kb_code,
        "name": f"/{kb_name}",
        "type": "directory",
        "size": 0,
    } in root_before.json()["data"]
    assert search_before.status_code == 200
    assert search_before.json()["data"]["meta"]["returned_count"] >= 1
    assert delete_response.status_code == 200
    assert root_after.status_code == 200
    assert root_after.json()["data"] == []
    assert read_after.status_code == 404
    assert search_after.status_code == 200
    assert search_after.json()["data"]["items"] == []


@pytest.mark.integration
def test_renaming_a_multilevel_directory_to_a_sibling_name_conflicts_without_state_change(
    monkeypatch, tmp_path
):
    """Sibling rename conflict should not alter the existing multilevel directory tree."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    conflict_code = f"dir-{uuid4().hex[:6]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=conflict_code,
            directory_path="/Policies/2025",
        )

        conflict = client.post(
            "/api/v1/directories/update",
            json={
                "kb_code": kb_code,
                "directory_code": conflict_code,
                "directory_name": "2024",
            },
        )
        parent_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["error_code"] == "KB_DIRECTORY_NAME_CONFLICT"
    assert parent_after.status_code == 200
    assert parent_after.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024",
            "type": "directory",
            "size": 0,
        },
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2025",
            "type": "directory",
            "size": 0,
        },
    ]


@pytest.mark.integration
def test_import_rejects_file_path_binding_conflict_without_overwriting_existing_file(
    monkeypatch, tmp_path
):
    """Importing a different file_code into the same path should fail without replacing the original."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    original_code = f"file-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=original_code,
            file_path="Policies/bound.md",
            markdown_content="original binding content\n",
        )
        conflict = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/bound.md",
                "file_description": "conflict file",
                "file_content": base64.b64encode(b"conflict").decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": "conflict content\n",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "conflict content",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )
        list_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )
        read_after = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/bound.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )

    assert conflict.status_code == 422
    assert conflict.json()["error"]["error_code"] == "KB_IMPORT_INVALID"
    assert list_after.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/bound.md",
            "type": "file",
            "size": len("original binding content\n".encode("utf-8")),
        }
    ]
    assert read_after.status_code == 200
    assert read_after.json()["data"]["data"] == "original binding content\n"


@pytest.mark.integration
def test_soft_deleted_file_code_cannot_be_reused_for_write_file(monkeypatch, tmp_path):
    """A soft-deleted file_code should still be reserved and return the standard conflict."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    file_code = f"file-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=file_code,
            file_path="Policies/reuse.md",
            markdown_content="first version\n",
        )
        delete_response = client.post(
            "/api/v1/knowledge-items/delete",
            json={"kb_code": kb_code, "file_code": file_code},
        )
        reuse = client.post(
            "/api/v1/write-file",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "file_path": "Policies/reuse-new.md",
                "file_description": "reuse attempt",
                "file_content": base64.b64encode(b"second").decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )

    assert delete_response.status_code == 200
    assert reuse.status_code == 409
    assert reuse.json()["error"]["error_code"] == "KB_FILE_CODE_SOFT_DELETED_CONFLICT"


@pytest.mark.integration
def test_soft_deleted_file_code_cannot_be_reused_for_import(monkeypatch, tmp_path):
    """A soft-deleted file_code should also be rejected by the atomic import endpoint."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    file_code = f"file-{uuid4().hex[:8]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=file_code,
            file_path="Policies/reimport.md",
            markdown_content="first import\n",
        )
        delete_response = client.post(
            "/api/v1/knowledge-items/delete",
            json={"kb_code": kb_code, "file_code": file_code},
        )
        reuse = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": file_code,
                "file_path": "Policies/reimport-v2.md",
                "file_description": "reimport attempt",
                "file_content": base64.b64encode(b"second").decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": "second import\n",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "second import",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )

    assert delete_response.status_code == 200
    assert reuse.status_code == 409
    assert reuse.json()["error"]["error_code"] == "KB_FILE_CODE_SOFT_DELETED_CONFLICT"


@pytest.mark.integration
def test_root_browse_multi_level_browse_and_full_markdown_read_work_together(
    monkeypatch, tmp_path
):
    """Root browse, nested browse, and full markdown read should line up on the same file tree."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    markdown_content = "full-1\nfull-2\nfull-3\n"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies/2024",
        )
        _import_markdown_file(
            client,
            kb_code=kb_code,
            file_code=f"file-{uuid4().hex[:8]}",
            file_path="Policies/2024/full.md",
            markdown_content=markdown_content,
        )
        root = client.post(
            "/api/v1/list_dir", json={"kb_codes": [kb_code], "path": "/"}
        )
        kb_root = client.post(
            "/api/v1/list_dir", json={"kb_codes": [kb_code], "path": kb_name}
        )
        nested = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies/2024"},
        )
        full_read = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": [kb_code],
                "path": f"{kb_name}/Policies/2024/full.md",
                "content_type": "markdown",
            },
        )

    assert root.status_code == 200
    assert {
        "kb_code": kb_code,
        "name": f"/{kb_name}",
        "type": "directory",
        "size": 0,
    } in root.json()["data"]
    assert kb_root.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies",
            "type": "directory",
            "size": 0,
        }
    ]
    assert nested.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/2024/full.md",
            "type": "file",
            "size": len(markdown_content.encode("utf-8")),
        }
    ]
    assert full_read.status_code == 200
    assert full_read.json()["data"]["data"] == markdown_content
    assert full_read.json()["data"]["reached_eof"] is True


@pytest.mark.integration
def test_root_browse_lists_multiple_knowledge_bases(monkeypatch):
    """Root browse should show multiple KB roots at once."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_one_code = f"kb-{uuid4().hex[:8]}"
    kb_one_name = f"KB One {uuid4().hex[:4]}"
    kb_two_code = f"kb-{uuid4().hex[:8]}"
    kb_two_name = f"KB Two {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_one_code, kb_one_name)
        _create_kb(client, kb_two_code, kb_two_name)
        root = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_one_code, kb_two_code], "path": "/"},
        )

    assert root.status_code == 200
    assert sorted(root.json()["data"], key=lambda item: item["name"]) == [
        {
            "kb_code": kb_one_code,
            "name": f"/{kb_one_name}",
            "type": "directory",
            "size": 0,
        },
        {
            "kb_code": kb_two_code,
            "name": f"/{kb_two_name}",
            "type": "directory",
            "size": 0,
        },
    ]


@pytest.mark.integration
def test_import_rejects_duplicate_chunk_numbers_with_request_validation_error(
    monkeypatch, tmp_path
):
    """Duplicate chunk numbers should be rejected before the import pipeline runs."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        response = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/dup.md",
                "file_description": "dup",
                "file_content": base64.b64encode(b"dup").decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": "dup\n",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "dup-1",
                        "embedding": [0.1, 0.2, 0.3],
                    },
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "dup-2",
                        "embedding": [0.1, 0.2, 0.3],
                    },
                ],
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"


@pytest.mark.integration
def test_build_stage_failure_prevents_any_knowledge_base_write_side_effects(
    monkeypatch, tmp_path
):
    """If knowledge_build fails, the KB should remain unchanged because no write call is made."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="   \n"),
    )

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    original_bytes = b"%PDF-1.4 no-write"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        markdown_content = _build_markdown_via_api(
            client, original_bytes=original_bytes
        )
        build_index = client.post(
            "/api/v1/build-markdown-index",
            json={"content": markdown_content},
        )
        list_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )

    assert build_index.status_code == 422
    assert build_index.json()["error"]["error_code"] == "CHUNK_EMPTY"
    assert list_after.status_code == 200
    assert list_after.json()["data"] == []


@pytest.mark.integration
def test_list_dir_returns_configuration_error_when_runtime_service_fails(monkeypatch):
    """A runtime service configuration failure should surface through list_dir."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)
    monkeypatch.setattr(
        main_module,
        "get_knowledge_base_service",
        lambda: (_ for _ in ()).throw(
            KnowledgeBaseConfigurationError("KB runtime is not configured")
        ),
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": ["demo"], "path": "/"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"


@pytest.mark.integration
def test_read_file_returns_configuration_error_when_runtime_service_fails(monkeypatch):
    """read-file should surface KB runtime configuration failures via the standard envelope."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)
    monkeypatch.setattr(
        main_module,
        "get_knowledge_base_service",
        lambda: (_ for _ in ()).throw(
            KnowledgeBaseConfigurationError("KB runtime is not configured")
        ),
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/read-file",
            json={
                "kb_codes": ["demo"],
                "path": "Demo/path.md",
                "content_type": "markdown",
                "start_line": 1,
                "end_line": 1,
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"


@pytest.mark.integration
def test_write_file_returns_configuration_error_when_runtime_service_fails(monkeypatch):
    """write-file should surface KB runtime configuration failures via the standard envelope."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)
    monkeypatch.setattr(
        main_module,
        "get_knowledge_item_ingestion_service",
        lambda: (_ for _ in ()).throw(
            KnowledgeBaseConfigurationError("KB runtime is not configured")
        ),
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/write-file",
            json={
                "kb_code": "demo",
                "file_code": "file-demo",
                "file_path": "Policies/demo.md",
                "file_description": "demo",
                "file_content": base64.b64encode(b"demo").decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"


@pytest.mark.integration
def test_search_returns_configuration_error_when_runtime_service_fails(monkeypatch):
    """search should surface KB runtime configuration failures via the standard envelope."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)
    monkeypatch.setattr(
        main_module,
        "get_knowledge_item_search_service",
        lambda: (_ for _ in ()).throw(
            KnowledgeBaseConfigurationError("KB runtime is not configured")
        ),
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/knowledge-items/search",
            json={"query": "demo", "kb_codes": ["demo"]},
        )

    assert response.status_code == 503
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"


@pytest.mark.integration
def test_search_supports_multi_kb_and_multi_filter_combinations(monkeypatch, tmp_path):
    """Search should honor combined kb/source/type filters across multiple knowledge bases."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_search_service(monkeypatch, settings)

    kb_one_code = f"kb-{uuid4().hex[:8]}"
    kb_one_name = f"KB One {uuid4().hex[:4]}"
    kb_two_code = f"kb-{uuid4().hex[:8]}"
    kb_two_name = f"KB Two {uuid4().hex[:4]}"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_one_code, kb_one_name)
        _create_directory(
            client,
            kb_code=kb_one_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        _create_kb(client, kb_two_code, kb_two_name)
        _create_directory(
            client,
            kb_code=kb_two_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        first = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_one_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/one.md",
                "file_description": "one",
                "file_content": base64.b64encode(b"annual leave matrix\n").decode(
                    "ascii"
                ),
                "version": "v1",
                "source_code": "hr",
                "status": "ACTIVE",
                "markdown_content": "annual leave matrix\n",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "annual leave matrix",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )
        second = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_two_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/two.txt",
                "file_description": "two",
                "file_content": base64.b64encode(b"annual leave matrix\n").decode(
                    "ascii"
                ),
                "version": "v1",
                "source_code": "finance",
                "status": "ACTIVE",
                "markdown_content": "annual leave matrix\n",
                "chunks": [
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "chunk_text": "annual leave matrix",
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
            },
        )
        filtered = client.post(
            "/api/v1/knowledge-items/search",
            json={
                "query": "annual leave matrix",
                "kb_codes": [kb_one_code, kb_two_code],
                "source_codes": ["hr"],
                "type_codes": ["md"],
                "top_k": 10,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert filtered.status_code == 200
    items = filtered.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["kb_code"] == kb_one_code
    assert items[0]["source_code"] == "hr"
    assert items[0]["type_code"] == "md"


@pytest.mark.integration
def test_import_failure_does_not_block_follow_up_successful_import(
    monkeypatch, tmp_path
):
    """A failed import should not poison later successful imports in the same directory."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Retry\n\nrecoverable flow\n"),
    )

    kb_code = f"kb-{uuid4().hex[:8]}"
    kb_name = f"Integration KB {uuid4().hex[:4]}"
    original_bytes = b"%PDF-1.4 retry flow"

    with TestClient(main_module.app) as client:
        _create_kb(client, kb_code, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_code=f"dir-{uuid4().hex[:6]}",
            directory_path="/Policies",
        )
        markdown_content = _build_markdown_via_api(
            client, original_bytes=original_bytes
        )
        chunks = _build_chunks_via_api(client, markdown_content=markdown_content)
        bad_chunks = [dict(chunks[0], embedding=[0.1, 0.2])]

        failed = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/failed.pdf",
                "file_description": "failed",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": markdown_content,
                "chunks": bad_chunks,
            },
        )
        succeeded = client.post(
            "/api/v1/knowledge-items/import",
            json={
                "kb_code": kb_code,
                "file_code": f"file-{uuid4().hex[:8]}",
                "file_path": "Policies/succeeded.pdf",
                "file_description": "succeeded",
                "file_content": base64.b64encode(original_bytes).decode("ascii"),
                "version": "v1",
                "source_code": "integration",
                "status": "ACTIVE",
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
        )
        list_after = client.post(
            "/api/v1/list_dir",
            json={"kb_codes": [kb_code], "path": f"{kb_name}/Policies"},
        )

    assert failed.status_code == 422
    assert succeeded.status_code == 200
    assert list_after.json()["data"] == [
        {
            "kb_code": kb_code,
            "name": f"/{kb_name}/Policies/succeeded.pdf",
            "type": "file",
            "size": len(original_bytes),
        }
    ]


@pytest.mark.integration
def test_create_kb_returns_configuration_error_when_runtime_settings_are_incomplete(
    monkeypatch,
):
    """API should surface a configuration error when KB runtime settings are incomplete."""
    broken_settings = _kb_settings().model_copy(update={"kb_minio_endpoint": ""})
    _reset_runtime(monkeypatch, broken_settings)
    _disable_kb_lifecycle(monkeypatch)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/knowledge-bases/create",
            json={
                "kb_code": f"kb-{uuid4().hex[:8]}",
                "kb_name": "Broken Config KB",
                "status": "ACTIVE",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"
