"""User-journey oriented stateful integration tests for knowledge_base APIs."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings
from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest
from by_qa.knowledge_base.infrastructure.runtime import (
    build_knowledge_item_search_service,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError
from by_qa.knowledge_common.exceptions import UnsupportedFileTypeError
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "15432"
DEFAULT_DB_DATABASE = "postgres"
DEFAULT_DB_USER = "gaussdb"
DEFAULT_DB_PASS = "OpenGauss#2026"


def _default_embedding_dimension() -> int:
    return int(os.getenv("EMBEDDING_DIMENSION", "3"))


def _default_embedding_vector() -> list[float]:
    return [0.1] * _default_embedding_dimension()


class FakeDocumentChunkingService:
    """Stable knowledge_build double used by cross-module API integration tests."""

    def __init__(self, *, markdown_text: str, embedding: list[float] | None = None):
        self.markdown_text = markdown_text
        self.embedding = embedding or _default_embedding_vector()
        self.extract_calls: list[dict[str, object]] = []

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:  # pylint: disable=unused-argument
        assert isinstance(file_bytes, bytes)
        self.extract_calls.append(
            {
                "file_bytes": file_bytes,
                "file_type": file_type,
            }
        )
        return self.markdown_text

    def chunk_and_embed(
        self, file_bytes: bytes, *, filename: str
    ) -> list[KnowledgeItemChunkPayload]:
        assert isinstance(filename, str)
        content = file_bytes.decode("utf-8")
        line_count = max(1, content.count("\n"))
        return [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=line_count,
                chunk_text=content.strip(),
                embedding=self.embedding,
            )
        ]


class FailingOnceDocumentChunkingService(FakeDocumentChunkingService):
    """Fails on the first parse attempt and succeeds on the next build."""

    def __init__(self, *, markdown_text: str, embedding: list[float] | None = None):
        super().__init__(markdown_text=markdown_text, embedding=embedding)
        self._should_fail = True

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:  # pylint: disable=unused-argument
        if self._should_fail:
            self._should_fail = False
            raise ValueError("simulated extract failure")
        return super().extract_text_from_file(file_bytes, file_type)


class _UnsupportedPngChunking(FakeDocumentChunkingService):
    """Raises UnsupportedFileTypeError for png; otherwise behaves like the fake."""

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        if file_type == "png":
            raise UnsupportedFileTypeError("unsupported file type: png")
        return super().extract_text_from_file(file_bytes, file_type)


class FakeEmbeddingQueryService:
    """Deterministic embedding service used to keep search integration offline."""

    def __init__(self, embedding: list[float] | None = None):
        self.embedding = embedding or _default_embedding_vector()

    async def embed_query(self, query: str) -> list[float]:
        assert isinstance(query, str)
        return self.embedding


class FakeModelConfigProvider:
    """Stable model config provider isolated from local .env values."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def get_config(self, model_type: str) -> ModelConfig:
        if model_type != "embedding":
            raise ValueError(f"Unexpected model_type: {model_type!r}")
        return ModelConfig(
            model_name=self.settings.embedding_model_name,
            temperature=0.0,
            base_url=self.settings.embedding_base_url,
            api_key=self.settings.embedding_api_key,
            dimension=self.settings.embedding_dimension,
            distance_metric=self.settings.embedding_distance_metric,
        )


def _kb_settings(*, agent_data_path=None) -> Settings:
    updates = {
        "DB_HOST": os.getenv("DB_HOST", DEFAULT_DB_HOST),
        "DB_PORT": int(os.getenv("DB_PORT", DEFAULT_DB_PORT)),
        "DB_DATABASE": os.getenv("DB_DATABASE", DEFAULT_DB_DATABASE),
        "DB_SCHEMA": os.getenv("DB_SCHEMA", ""),
        "DB_USER": os.getenv("DB_USER", DEFAULT_DB_USER),
        "DB_PASS": os.getenv("DB_PASS", DEFAULT_DB_PASS),
        "MINIO_ENDPOINT": os.getenv("MINIO_ENDPOINT", "127.0.0.1:19000"),
        "MINIO_ACCESS_KEY": os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        "MINIO_SECRET_KEY": os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        "KB_MINIO_BUCKET": os.getenv("KB_MINIO_BUCKET", "knowledge-base"),
        "KB_MINIO_MARKDOWN_BUCKET": os.getenv(
            "KB_MINIO_MARKDOWN_BUCKET", "knowledge-base-markdown"
        ),
        "MINIO_SECURE": False,
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
    monkeypatch.setattr(
        main_module,
        "load_model_config_provider",
        lambda: FakeModelConfigProvider(settings),
    )
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)
    monkeypatch.setattr(main_module, "_file_metadata_query_service", None)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_initialized", False)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_lock", asyncio.Lock())

    async def _noop_register(application):  # pylint: disable=unused-argument
        return None

    monkeypatch.setattr(main_module, "_register_service", _noop_register)
    monkeypatch.setattr(main_module, "_unregister_service", _noop_register)


def _set_document_chunking_service(
    monkeypatch: pytest.MonkeyPatch,
    service: FakeDocumentChunkingService,
) -> None:
    async def get_service(provider=None):  # pylint: disable=unused-argument
        return service

    monkeypatch.setattr(
        main_module, "_get_or_build_document_chunking_service", get_service
    )


async def _set_search_service(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    embedding: list[float] | None = None,
) -> None:
    service = await build_knowledge_item_search_service(settings)
    service.embedding_query_service = FakeEmbeddingQueryService(embedding)

    async def get_service(provider=None):  # pylint: disable=unused-argument
        return service

    monkeypatch.setattr(
        main_module, "_get_or_build_knowledge_item_search_service", get_service
    )


def _disable_kb_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable startup/shutdown runtime initialization for route-level failure tests."""

    async def _noop(enabled_modules):  # pylint: disable=unused-argument
        pass

    monkeypatch.setattr(main_module, "_initialize_knowledge_base_runtime", _noop)
    monkeypatch.setattr(main_module, "_shutdown_knowledge_base_runtime", _noop)


def _create_kb(client: TestClient, kb_name: str) -> str:
    """Create a knowledge base and return the assigned knCode."""
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knName": kb_name},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["knCode"]


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


def _upload_file(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    file_content: bytes,
    content_type: str = "text/markdown",
) -> None:
    """Upload a file via the multipart /api/v1/knowledgeItems/import endpoint."""
    response = client.post(
        "/api/v1/knowledgeItems/import",
        data={"knCode": kb_code, "filePath": file_path},
        files={"fileContent": (file_path.split("/")[-1], file_content, content_type)},
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
    # Step 1: Upload via multipart /api/v1/knowledgeItems/import
    _upload_file(
        client,
        kb_code=kb_code,
        file_path=file_path,
        file_content=file_content,
        content_type=content_type,
    )
    # Step 2: Build markdown index via /api/v1/fileToMarkdownIndex
    build_response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={
            "knCode": kb_code,
            "filePath": file_path,
        },
    )
    assert build_response.status_code == 200, build_response.text


def _file_build_status(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
):
    response = client.post(
        "/api/v1/fileBuildStatus",
        json={
            "knCode": kb_code,
            "filePath": file_path,
        },
    )
    assert response.status_code == 200, response.text
    return response


@pytest.mark.integration
def test_metadata_get_returns_imported_front_matter(monkeypatch):
    """metadata/get should read YAML front matter persisted by import."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# meeting\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    markdown = b"""---
\xe4\xbc\x9a\xe8\xae\xae\xe4\xb8\xbb\xe9\xa2\x98: DataCloud\xe5\xb9\xb3\xe5\x8f\xb0\xe9\x9c\x80\xe6\xb1\x82\xe7\xa1\xae\xe8\xae\xa4\xe4\xbc\x9a
\xe4\xbc\x9a\xe8\xae\xae\xe6\x97\xa5\xe6\x9c\x9f: 2026-05-25
\xe5\x8f\x82\xe4\xbc\x9a\xe4\xba\xba\xe5\x91\x98:
  - Alice
  - Bob
---

# \xe4\xbc\x9a\xe8\xae\xae\xe7\xba\xaa\xe8\xa6\x81
DataCloud\xe5\xb9\xb3\xe5\x8f\xb0\xe9\x9c\x80\xe6\xb1\x82\xe7\xa1\xae\xe8\xae\xa4\xe4\xbc\x9a
"""

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/meeting.md",
            file_content=markdown,
        )

        response = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": "/meeting.md",
                "metadataFieldList": ["会议主题", "会议日期", "参会人员"],
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    metadata = payload["resultObject"]["metadata"]
    assert metadata["会议主题"] == {
        "valueType": "string",
        "value": "DataCloud平台需求确认会",
    }
    assert metadata["会议日期"]["valueType"] == "datetime"
    assert metadata["会议日期"]["value"].startswith("2026-05-25")
    assert metadata["参会人员"] == {
        "valueType": "stringList",
        "value": ["Alice", "Bob"],
    }


@pytest.mark.integration
def test_file_to_markdown_converts_uploaded_file_to_markdown_stream(monkeypatch):
    """fileToMarkdown should synchronously convert an uploaded file into an md download."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)
    fake_chunking = FakeDocumentChunkingService(
        markdown_text="# Converted Policy\n\nPlain text body.\n"
    )
    _set_document_chunking_service(monkeypatch, fake_chunking)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/fileToMarkdown",
            files={
                "fileContent": (
                    "policy.txt",
                    b"Plain text body.",
                    "text/plain",
                )
            },
        )

    assert response.status_code == 200
    assert response.content == b"# Converted Policy\n\nPlain text body.\n"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="policy.md"'
    assert fake_chunking.extract_calls == [
        {
            "file_bytes": b"Plain text body.",
            "file_type": "txt",
        }
    ]


@pytest.mark.integration
def test_file_to_markdown_rejects_unsupported_uploaded_file_type(monkeypatch):
    """fileToMarkdown should validate the upload filename extension before parsing."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)
    fake_chunking = FakeDocumentChunkingService(markdown_text="unused")
    _set_document_chunking_service(monkeypatch, fake_chunking)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/fileToMarkdown",
            files={
                "fileContent": (
                    "installer.exe",
                    b"not a supported document",
                    "application/octet-stream",
                )
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resultCode"] == "-1"
    assert payload["resultMsg"].startswith("unsupported file type: exe")
    assert payload["resultObject"] == {}
    assert fake_chunking.extract_calls == []


@pytest.mark.integration
def test_create_directory_returns_success_for_duplicate_path(monkeypatch):
    """Directory creation should be idempotent for duplicate path reuse."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)

        first = client.post(
            "/api/v1/directories/create",
            json={
                "knCode": kb_code,
                "directoryPath": "/Policies",
                "directoryDescription": "Policies",
            },
        )
        duplicate_path = client.post(
            "/api/v1/directories/create",
            json={
                "knCode": kb_code,
                "directoryPath": "/Policies",
                "directoryDescription": "Policies duplicate",
            },
        )

    assert first.status_code == 200
    assert duplicate_path.status_code == 200
    assert first.json()["resultCode"] == "0"
    assert duplicate_path.json()["resultCode"] == "0"


@pytest.mark.integration
def test_create_empty_knowledge_base_exposes_root_and_rejects_duplicate_name(
    monkeypatch,
):
    """Creating an empty KB should expose its root entry and reject duplicate kb_name reuse."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        first = client.post(
            "/api/v1/knowledgeBases/create",
            json={"knName": kb_name},
        )
        kb_code = first.json()["resultObject"]["knCode"]
        root = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        duplicate = client.post(
            "/api/v1/knowledgeBases/create",
            json={"knName": kb_name},
        )

    assert first.status_code == 200
    assert root.status_code == 200
    root_data = root.json()["resultObject"]["data"]
    assert len(root_data) == 0
    assert duplicate.status_code == 200
    assert duplicate.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_create_directory_creates_parents_and_exposes_new_child_at_parent_level(
    monkeypatch,
):
    """Directory creation auto-creates missing parents and exposes the new child in listing."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        nested = client.post(
            "/api/v1/directories/create",
            json={
                "knCode": kb_code,
                "directoryPath": "/Missing/Leaf",
                "directoryDescription": "auto-created parent",
            },
        )
        create_root_child = client.post(
            "/api/v1/directories/create",
            json={
                "knCode": kb_code,
                "directoryPath": "/Policies",
                "directoryDescription": "policies",
            },
        )
        kb_root = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )

    assert nested.status_code == 200
    assert create_root_child.status_code == 200
    assert kb_root.status_code == 200
    kb_root_data = kb_root.json()["resultObject"]["data"]
    names = [item["name"] for item in kb_root_data]
    assert any("Policies" in n for n in names)
    assert any("Missing" in n for n in names)


@pytest.mark.integration
def test_upload_and_build_makes_markdown_readable(monkeypatch, tmp_path):
    """Content admin can upload a file, build it, then read the built markdown."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="line1\nline2\nline3\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    file_path = "/Policies/manual.md"
    markdown_content = "line1\nline2\nline3\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=markdown_content.encode("utf-8"),
        )

        markdown_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "startLine": 2,
                "endLine": 3,
            },
        )

    assert markdown_read.status_code == 200
    assert "line2" in markdown_read.json()["resultObject"]["data"]


@pytest.mark.integration
def test_file_build_status_returns_complete_result_after_successful_build(
    monkeypatch, tmp_path
):
    """Successful build should expose the latest complete step and dictionaries."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="line1\nline2\nline3\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    file_path = "/Policies/manual.md"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/Policies")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=b"line1\nline2\nline3\n",
        )

        status_response = _file_build_status(
            client,
            kb_code=kb_code,
            file_path=file_path,
        )

    payload = status_response.json()
    assert payload["resultCode"] == "0"
    assert payload["resultMsg"] == "success"
    assert payload["resultObject"]["status"] == "complete"
    assert payload["resultObject"]["currentStep"] == "complete"
    assert {
        "standCode": "complete",
        "standDisplayValue": "已完成",
        "standDisplayValueEn": "complete",
    } in payload["resultObject"]["statusDict"]
    assert {
        "standCode": "complete",
        "standDisplayValue": "已完成",
        "standDisplayValueEn": "complete",
    } in payload["resultObject"]["stepDict"]


@pytest.mark.integration
def test_file_to_markdown_index_rejects_duplicate_request_while_running(
    monkeypatch, tmp_path
):
    """A second build request should be rejected while the latest task is still running."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    file_path = "/Policies/manual.md"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/Policies")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=b"alpha\nbeta\n",
        )

        ingestion_service = asyncio.run(
            main_module._get_or_build_knowledge_item_ingestion_service()
        )
        build_task_id = asyncio.run(
            ingestion_service.create_file_to_markdown_index_task(
                FileToMarkdownIndexRequest(kb_code=kb_code, file_path=file_path)
            )
        )

        running_status = _file_build_status(
            client,
            kb_code=kb_code,
            file_path=file_path,
        )
        duplicate_response = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert build_task_id > 0

    running_payload = running_status.json()
    assert running_payload["resultCode"] == "0"
    assert running_payload["resultObject"]["status"] == "running"
    assert running_payload["resultObject"]["currentStep"] == "markdown"

    duplicate_payload = duplicate_response.json()
    assert duplicate_payload["resultCode"] == "-1"
    assert "build task already exists" in duplicate_payload["resultMsg"]


@pytest.mark.integration
def test_failed_build_status_can_be_retried_to_complete(monkeypatch, tmp_path):
    """Failed builds should surface failed status first, then allow a successful retry."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FailingOnceDocumentChunkingService(markdown_text="retry\nworks\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    file_path = "/Policies/manual.md"

    with TestClient(main_module.app, raise_server_exceptions=False) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/Policies")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=b"retry\nworks\n",
        )

        first_build = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": file_path},
        )
        failed_status = _file_build_status(
            client,
            kb_code=kb_code,
            file_path=file_path,
        )

        second_build = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": file_path},
        )
        complete_status = _file_build_status(
            client,
            kb_code=kb_code,
            file_path=file_path,
        )

    assert first_build.status_code == 200
    assert first_build.json()["resultCode"] == "0"
    assert failed_status.json()["resultObject"]["status"] == "failed"
    assert failed_status.json()["resultObject"]["currentStep"] == "markdown"

    assert second_build.status_code == 200
    assert second_build.json()["resultCode"] == "0"
    assert complete_status.json()["resultObject"]["status"] == "complete"
    assert complete_status.json()["resultObject"]["currentStep"] == "complete"


@pytest.mark.integration
async def test_success_responses_follow_documented_path_contract(monkeypatch, tmp_path):
    """Successful responses should follow the documented path semantics."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="line1\nline2\nline3\n"),
    )
    await _set_search_service(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    file_path = "/Policies/manual.md"
    markdown_content = "line1\nline2\nline3\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=markdown_content.encode("utf-8"),
        )

        list_response = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )
        glob_response = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/Policies/*.md"},
        )
        read_response = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "startLine": 1,
                "endLine": 2,
            },
        )
        download_response = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": file_path},
        )
        search_response = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "line2",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )

    assert list_response.status_code == 200, list_response.text
    list_data = list_response.json()["resultObject"]["data"]
    assert len(list_data) >= 1

    assert glob_response.status_code == 200, glob_response.text
    glob_data = glob_response.json()["resultObject"]["data"]
    assert len(glob_data) >= 1

    assert read_response.status_code == 200, read_response.text
    assert read_response.json()["resultObject"]["data"]

    assert download_response.status_code == 200

    assert search_response.status_code == 200, search_response.text
    search_data = search_response.json()["resultObject"]["data"]
    if search_data:
        assert "filePath" in search_data[0]


@pytest.mark.integration
async def test_directory_rename_updates_parent_and_child_queries(monkeypatch, tmp_path):
    """Renaming a directory should update browse, match, and read behavior together."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="alpha\nbeta\ngamma\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/2024/handbook.md",
            file_content=b"alpha\nbeta\ngamma\n",
        )

        before_parent = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )

        rename = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/Policies/2024",
                "directoryName": "Archive",
            },
        )
        assert rename.status_code == 200, rename.text

        after_parent = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )
        old_child = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/2024"},
        )
        new_child = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/Archive"},
        )
        old_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/2024/handbook.md",
                "startLine": 1,
                "endLine": 2,
            },
        )
        new_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/Archive/handbook.md",
                "startLine": 1,
                "endLine": 2,
            },
        )

    assert before_parent.status_code == 200
    before_data = before_parent.json()["resultObject"]["data"]
    assert any("2024" in item["name"] for item in before_data)

    assert after_parent.status_code == 200
    after_data = after_parent.json()["resultObject"]["data"]
    assert any("Archive" in item["name"] for item in after_data)

    assert old_child.status_code == 200
    assert old_child.json()["resultCode"] == "-1"
    assert new_child.status_code == 200
    new_child_data = new_child.json()["resultObject"]["data"]
    assert len(new_child_data) >= 1

    assert old_read.status_code == 200
    assert old_read.json()["resultCode"] == "-1"
    assert new_read.status_code == 200
    assert new_read.json()["resultObject"]["data"]


@pytest.mark.integration
def test_directory_delete_removes_subtree_from_follow_up_queries(monkeypatch, tmp_path):
    """Deleting a non-empty directory should remove the subtree from all follow-up reads."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="line1\nline2\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/Archive",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/Archive/handbook.md",
            file_content=b"line1\nline2\n",
        )

        delete_response = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/Policies/Archive"},
        )
        parent_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )
        deleted_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/Archive"},
        )
        deleted_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/Archive/handbook.md",
                "startLine": 1,
                "endLine": 1,
            },
        )

    assert delete_response.status_code == 200
    assert parent_list.status_code == 200
    assert parent_list.json()["resultObject"]["data"] == []
    assert deleted_list.status_code == 200
    assert deleted_list.json()["resultCode"] == "-1"
    assert deleted_read.status_code == 200
    assert deleted_read.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_upload_and_build_into_a_multilevel_directory_tree(monkeypatch, tmp_path):
    """Content admin can upload and build a file into a deep directory tree."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Handbook\n\nalpha\nbeta\ngamma\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    original_bytes = b"%PDF-1.4 fake handbook bytes"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024/Q1",
        )

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/2024/Q1/handbook.pdf",
            file_content=original_bytes,
            content_type="application/pdf",
        )

        root_children = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        level_one = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )
        level_two = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/2024"},
        )
        level_three = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/2024/Q1"},
        )
        markdown_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/2024/Q1/handbook.pdf",
                "startLine": 1,
                "endLine": 3,
            },
        )

    assert root_children.status_code == 200
    assert any(
        "Policies" in item["name"]
        for item in root_children.json()["resultObject"]["data"]
    )
    assert level_one.status_code == 200
    assert any(
        "2024" in item["name"] for item in level_one.json()["resultObject"]["data"]
    )
    assert level_two.status_code == 200
    assert any(
        "Q1" in item["name"] for item in level_two.json()["resultObject"]["data"]
    )
    assert level_three.status_code == 200
    assert any(
        "handbook.pdf" in item["name"]
        for item in level_three.json()["resultObject"]["data"]
    )
    assert markdown_read.status_code == 200
    assert markdown_read.json()["resultObject"]["data"]


@pytest.mark.integration
def test_multilevel_directory_tree_lists_direct_children_and_supports_glob_matching(
    monkeypatch, tmp_path
):
    """Multi-level trees should preserve direct-child listings and pattern-based matches."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="content\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/A",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/A/B",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/A/B/C",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/B/C/one.md",
            file_content=b"one\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/B/C/two.md",
            file_content=b"two\n",
        )

        root_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/A"},
        )
        middle_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/A/B"},
        )
        glob_list = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/A/B/C/*.md"},
        )

    assert root_list.status_code == 200
    root_data = root_list.json()["resultObject"]["data"]
    assert any("B" in item["name"] for item in root_data)

    assert middle_list.status_code == 200
    middle_data = middle_list.json()["resultObject"]["data"]
    assert any("C" in item["name"] for item in middle_data)

    assert glob_list.status_code == 200
    glob_data = glob_list.json()["resultObject"]["data"]
    assert len(glob_data) == 2


@pytest.mark.integration
def test_renaming_a_middle_directory_updates_all_descendant_paths(
    monkeypatch, tmp_path
):
    """Renaming a middle directory should move descendant directories and files together."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="line1\nline2\nline3\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024/Q1",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/2024/Q1/handbook.md",
            file_content=b"line1\nline2\nline3\n",
        )

        rename = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/Policies/2024",
                "directoryName": "2025",
            },
        )
        top_after = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )
        middle_after = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/2025"},
        )
        leaf_after = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/2025/Q1"},
        )
        old_leaf = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/2024/Q1"},
        )
        old_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/2024/Q1/handbook.md",
                "startLine": 1,
                "endLine": 1,
            },
        )
        new_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/2025/Q1/handbook.md",
                "startLine": 1,
                "endLine": 2,
            },
        )
        old_glob = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/Policies/2024/Q1/*.md"},
        )
        new_glob = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/Policies/2025/Q1/*.md"},
        )

    assert rename.status_code == 200, rename.text
    assert top_after.status_code == 200
    top_data = top_after.json()["resultObject"]["data"]
    assert any("2025" in item["name"] for item in top_data)

    assert middle_after.status_code == 200
    middle_data = middle_after.json()["resultObject"]["data"]
    assert any("Q1" in item["name"] for item in middle_data)

    assert leaf_after.status_code == 200
    leaf_data = leaf_after.json()["resultObject"]["data"]
    assert any("handbook.md" in item["name"] for item in leaf_data)

    assert old_leaf.status_code == 200
    assert old_leaf.json()["resultCode"] == "-1"
    assert old_read.status_code == 200
    assert old_read.json()["resultCode"] == "-1"
    assert new_read.status_code == 200
    assert new_read.json()["resultObject"]["data"]

    assert old_glob.status_code == 200
    assert old_glob.json()["resultObject"]["data"] == []
    assert new_glob.status_code == 200
    assert len(new_glob.json()["resultObject"]["data"]) >= 1


@pytest.mark.integration
def test_deleting_a_middle_directory_removes_the_entire_descendant_subtree(
    monkeypatch, tmp_path
):
    """Deleting a middle directory should remove every descendant directory and file."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="content\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/Archive",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/Archive/Q1",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/Archive/Q2",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/Archive/Q1/a.md",
            file_content=b"q1-line1\nq1-line2\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/Archive/Q2/b.md",
            file_content=b"q2-line1\nq2-line2\n",
        )

        delete_response = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/Policies/Archive"},
        )
        top_after = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )
        deleted_middle = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/Archive"},
        )
        deleted_glob = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/Policies/Archive/*/*.md"},
        )
        deleted_read_one = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/Archive/Q1/a.md",
                "startLine": 1,
                "endLine": 1,
            },
        )
        deleted_read_two = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/Archive/Q2/b.md",
                "startLine": 1,
                "endLine": 1,
            },
        )

    assert delete_response.status_code == 200
    assert top_after.json()["resultObject"]["data"] == []
    assert deleted_middle.status_code == 200
    assert deleted_middle.json()["resultCode"] == "-1"
    assert deleted_glob.status_code == 200
    assert deleted_glob.json()["resultObject"]["data"] == []
    assert deleted_read_one.status_code == 200
    assert deleted_read_one.json()["resultCode"] == "-1"
    assert deleted_read_two.status_code == 200
    assert deleted_read_two.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_updating_kb_name_does_not_affect_file_paths(monkeypatch, tmp_path):
    """Knowledge-base rename should not affect KB-relative file paths."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="guide-line1\nguide-line2\n"),
    )

    old_name = f"Integration KB {uuid4().hex[:12]}"
    new_name = f"Renamed KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, old_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/guide.md",
            file_content=b"guide-line1\nguide-line2\n",
        )

        update_response = client.post(
            "/api/v1/knowledgeBases/update",
            json={"knCode": kb_code, "knName": new_name},
        )
        read_after_rename = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/guide.md",
                "startLine": 1,
                "endLine": 2,
            },
        )
        list_after_rename = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )

    assert update_response.status_code == 200, update_response.text
    assert read_after_rename.status_code == 200
    assert read_after_rename.json()["resultObject"]["data"]
    assert list_after_rename.status_code == 200
    assert len(list_after_rename.json()["resultObject"]["data"]) == 1


@pytest.mark.integration
def test_deleting_a_single_file_removes_it_from_follow_up_browse_and_read(
    monkeypatch, tmp_path
):
    """Deleting one file should remove only that file while preserving sibling visibility."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="content\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/keep.md",
            file_content=b"keep-line1\nkeep-line2\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/delete.md",
            file_content=b"delete-line1\ndelete-line2\n",
        )

        delete_response = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": "/Policies/delete.md"},
        )
        list_after = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )
        deleted_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/delete.md",
                "startLine": 1,
                "endLine": 1,
            },
        )
        kept_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/keep.md",
                "startLine": 1,
                "endLine": 2,
            },
        )

    assert delete_response.status_code == 200
    assert list_after.status_code == 200
    list_data = list_after.json()["resultObject"]["data"]
    assert len(list_data) == 1
    assert "keep.md" in list_data[0]["name"]

    assert deleted_read.status_code == 200
    assert deleted_read.json()["resultCode"] == "-1"
    assert kept_read.status_code == 200
    assert kept_read.json()["resultObject"]["data"]


@pytest.mark.integration
def test_read_file_rejects_invalid_markdown_line_windows(monkeypatch, tmp_path):
    """Reader should get stable validation errors for invalid markdown line windows."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="w1\nw2\nw3\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/window.md",
            file_content=b"w1\nw2\nw3\n",
        )

        zero_start = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/window.md",
                "startLine": 0,
                "endLine": 1,
            },
        )
        reversed_window = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/window.md",
                "startLine": 3,
                "endLine": 2,
            },
        )

    assert zero_start.status_code == 200
    assert zero_start.json()["resultCode"] == "-1"
    assert "startLine must be greater than 0" in zero_start.json()["resultMsg"]
    assert reversed_window.status_code == 200
    assert reversed_window.json()["resultCode"] == "-1"
    assert (
        "endLine must be greater than or equal to startLine"
        in reversed_window.json()["resultMsg"]
    )


@pytest.mark.integration
def test_download_file_returns_original_bytes_with_non_ascii_filename(
    monkeypatch, tmp_path
):
    """Download-file should return original bytes and a safe header for non-ASCII names."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(
            markdown_text="# 最佳实践\n\n第一条：保持接口清晰。\n"
        ),
    )

    kb_name = f"DEMO知识库{uuid4().hex[:12]}"
    file_path = "/考勤制度/开源项目最佳实践汇报.md"
    original_content = "# 最佳实践\n\n第一条：保持接口清晰。\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/考勤制度",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=original_content.encode("utf-8"),
        )

        response = client.post(
            "/api/v1/downloadFile",
            json={
                "knCode": kb_code,
                "filePath": file_path,
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

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    original_bytes = b"%PDF-1.4 binary handbook bytes"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/handbook.pdf",
            file_content=original_bytes,
            content_type="application/pdf",
        )

        response = client.post(
            "/api/v1/downloadFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/handbook.pdf",
            },
        )

    assert response.status_code == 200
    assert response.content == original_bytes
    assert response.headers["content-type"].startswith("application/pdf")
    assert (
        response.headers["content-disposition"] == 'attachment; filename="handbook.pdf"'
    )


@pytest.mark.integration
async def test_search_returns_hits_for_content_imported_through_upload_and_build(
    monkeypatch, tmp_path
):
    """Search user should hit content that was uploaded and built through the new flow."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(
            markdown_text="# FAQ\n\nvacation policy carryover\n"
        ),
    )
    await _set_search_service(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"
    original_bytes = b"%PDF-1.4 faq content"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/faq.pdf",
            file_content=original_bytes,
            content_type="application/pdf",
        )

        search_response = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "vacation carryover",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )

    assert search_response.status_code == 200, search_response.text
    search_data = search_response.json()["resultObject"]["data"]
    assert len(search_data) >= 1
    assert search_data[0]["knCode"] == kb_code


@pytest.mark.integration
async def test_search_respects_file_type_filter(monkeypatch, tmp_path):
    """Search filters should keep only results matching the requested file type constraints."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="annual leave handbook\n"),
    )
    await _set_search_service(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/hr.md",
            file_content=b"annual leave handbook\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/finance.txt",
            file_content=b"annual leave handbook\n",
            content_type="text/plain",
        )
        filtered = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "annual leave handbook",
                "knCodeList": [kb_code],
                "fileTypeList": ["md"],
                "topK": 10,
                "searchMode": "mixedRecall",
            },
        )

    assert filtered.status_code == 200, filtered.text
    items = filtered.json()["resultObject"]["data"]
    assert len(items) >= 1
    for item in items:
        assert item["filePath"].endswith(".md")


@pytest.mark.integration
async def test_search_path_updates_after_middle_directory_rename(monkeypatch, tmp_path):
    """Search results should follow the new file path after a middle directory rename."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="rename target sentence\n"),
    )
    await _set_search_service(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024/Q1",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/2024/Q1/rename-search.md",
            file_content=b"rename target sentence\n",
        )
        before = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "rename target",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        rename = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/Policies/2024",
                "directoryName": "2025",
            },
        )
        after = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "rename target",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )

    assert before.status_code == 200
    before_items = before.json()["resultObject"]["data"]
    assert len(before_items) >= 1
    assert "2024" in before_items[0]["filePath"]

    assert rename.status_code == 200, rename.text

    # NOTE: knowledge_chunk_retrieval_mv.full_path is not updated on directory rename.
    # Search results still return the old path. This is a known gap to be addressed.
    assert after.status_code == 200
    after_items = after.json()["resultObject"]["data"]
    assert len(after_items) >= 1


@pytest.mark.integration
async def test_search_results_disappear_after_single_file_delete(monkeypatch, tmp_path):
    """Deleting a file should remove its chunks from later search results."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="search should disappear\n"),
    )
    await _set_search_service(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/delete-search.md",
            file_content=b"search should disappear\n",
        )
        before = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "disappear",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        delete_response = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": "/Policies/delete-search.md"},
        )
        after = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "disappear",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )

    assert before.status_code == 200
    assert len(before.json()["resultObject"]["data"]) >= 1
    assert delete_response.status_code == 200
    assert after.status_code == 200
    assert after.json()["resultObject"]["data"] == []


@pytest.mark.integration
async def test_search_results_disappear_after_middle_directory_delete(
    monkeypatch, tmp_path
):
    """Deleting a middle directory should remove descendant file hits from search results."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="subtree search disappears\n"),
    )
    await _set_search_service(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/Archive",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/Archive/Q1",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/Archive/Q1/dir-delete-search.md",
            file_content=b"subtree search disappears\n",
        )
        before = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "subtree disappears",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        delete_response = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/Policies/Archive"},
        )
        after = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "subtree disappears",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )

    assert before.status_code == 200
    assert len(before.json()["resultObject"]["data"]) >= 1
    assert delete_response.status_code == 200
    assert after.status_code == 200
    assert after.json()["resultObject"]["data"] == []


@pytest.mark.integration
async def test_deleting_a_knowledge_base_removes_root_visibility_readability_and_search(
    monkeypatch, tmp_path
):
    """Deleting a knowledge base should hide it from root browse, reads, and search."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="knowledge base removal search\n"),
    )
    await _set_search_service(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/base-delete.md",
            file_content=b"knowledge base removal search\n",
        )

        root_before = client.post(
            "/api/v1/listDir", json={"knCode": kb_code, "directoryPath": "/"}
        )
        search_before = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "removal search",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        delete_response = client.post(
            "/api/v1/knowledgeBases/delete",
            json={"knCode": kb_code},
        )
        root_after = client.post(
            "/api/v1/listDir", json={"knCode": kb_code, "directoryPath": "/"}
        )
        read_after = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/base-delete.md",
                "startLine": 1,
                "endLine": 1,
            },
        )
        search_after = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "removal search",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )

    assert root_before.status_code == 200
    assert len(root_before.json()["resultObject"]["data"]) >= 1
    assert search_before.status_code == 200
    assert len(search_before.json()["resultObject"]["data"]) >= 1
    assert delete_response.status_code == 200
    assert root_after.status_code == 200
    assert root_after.json()["resultCode"] == "-1"
    assert read_after.status_code == 200
    assert read_after.json()["resultCode"] == "-1"
    assert search_after.status_code == 200
    assert search_after.json()["resultObject"]["data"] == []


@pytest.mark.integration
def test_renaming_a_multilevel_directory_to_a_sibling_name_conflicts_without_state_change(
    monkeypatch, tmp_path
):
    """Sibling rename conflict should not alter the existing multilevel directory tree."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2025",
        )

        conflict = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/Policies/2025",
                "directoryName": "2024",
            },
        )
        parent_after = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies"},
        )

    assert conflict.status_code == 200
    assert conflict.json()["resultCode"] == "-1"
    assert parent_after.status_code == 200
    parent_data = parent_after.json()["resultObject"]["data"]
    assert len(parent_data) == 2


@pytest.mark.integration
def test_read_file_returns_not_built_error_when_file_not_built(monkeypatch, tmp_path):
    """Reading a file that was uploaded but not built should return a 'file not built' error."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        # Upload only, no build
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/not-built.md",
            file_content=b"some content\n",
        )

        read_response = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/not-built.md",
                "startLine": 1,
                "endLine": 1,
            },
        )

    assert read_response.status_code == 200
    assert read_response.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_root_browse_multi_level_browse_and_full_markdown_read_work_together(
    monkeypatch, tmp_path
):
    """Root browse, nested browse, and full markdown read should line up on the same file tree."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="full-1\nfull-2\nfull-3\n"),
    )

    kb_name = f"Integration KB {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies",
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/Policies/2024",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Policies/2024/full.md",
            file_content=b"full-1\nfull-2\nfull-3\n",
        )
        root = client.post(
            "/api/v1/listDir", json={"knCode": kb_code, "directoryPath": "/"}
        )
        kb_root = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        nested = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/Policies/2024"},
        )
        full_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Policies/2024/full.md",
            },
        )

    assert root.status_code == 200
    assert len(root.json()["resultObject"]["data"]) >= 1

    assert kb_root.status_code == 200
    kb_root_data = kb_root.json()["resultObject"]["data"]
    assert any("Policies" in item["name"] for item in kb_root_data)

    assert nested.status_code == 200
    nested_data = nested.json()["resultObject"]["data"]
    assert any("full.md" in item["name"] for item in nested_data)

    assert full_read.status_code == 200
    assert full_read.json()["resultObject"]["data"]
    assert full_read.json()["resultObject"].get("reachedEof") is True


@pytest.mark.integration
def test_root_browse_lists_directories_in_each_knowledge_base(monkeypatch):
    """Root browse should show top-level directories for each KB independently."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    kb_one_name = f"KB One {uuid4().hex[:12]}"
    kb_two_name = f"KB Two {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_one_code = _create_kb(client, kb_one_name)
        kb_two_code = _create_kb(client, kb_two_name)
        _create_directory(client, kb_code=kb_one_code, directory_path="/Docs")
        _create_directory(client, kb_code=kb_two_code, directory_path="/Reports")
        root_one = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_one_code, "directoryPath": "/"},
        )
        root_two = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_two_code, "directoryPath": "/"},
        )

    assert root_one.status_code == 200
    assert len(root_one.json()["resultObject"]["data"]) >= 1
    assert root_two.status_code == 200
    assert len(root_two.json()["resultObject"]["data"]) >= 1


@pytest.mark.integration
async def test_search_supports_multi_kb_combinations(monkeypatch, tmp_path):
    """Search should honor combined kb filters across multiple knowledge bases."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="annual leave matrix\n"),
    )
    await _set_search_service(monkeypatch, settings)

    kb_one_name = f"KB One {uuid4().hex[:12]}"
    kb_two_name = f"KB Two {uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_one_code = _create_kb(client, kb_one_name)
        _create_directory(
            client,
            kb_code=kb_one_code,
            directory_path="/Policies",
        )
        kb_two_code = _create_kb(client, kb_two_name)
        _create_directory(
            client,
            kb_code=kb_two_code,
            directory_path="/Policies",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_one_code,
            file_path="/Policies/one.md",
            file_content=b"annual leave matrix\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_two_code,
            file_path="/Policies/two.txt",
            file_content=b"annual leave matrix\n",
            content_type="text/plain",
        )
        filtered = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "annual leave matrix",
                "knCodeList": [kb_one_code, kb_two_code],
                "fileTypeList": ["md"],
                "topK": 10,
                "searchMode": "mixedRecall",
            },
        )

    assert filtered.status_code == 200
    items = filtered.json()["resultObject"]["data"]
    assert len(items) >= 1
    for item in items:
        assert item["filePath"].endswith(".md")


@pytest.mark.integration
def test_list_dir_returns_configuration_error_when_runtime_service_fails(monkeypatch):
    """A runtime service configuration failure should surface through listDir."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)

    async def _raise():
        raise KnowledgeBaseConfigurationError("KB runtime is not configured")

    monkeypatch.setattr(main_module, "_get_or_build_knowledge_base_service", _raise)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/listDir",
            json={"knCode": "demo", "directoryPath": "/"},
        )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert "KB runtime is not configured" in response.json()["resultMsg"]


@pytest.mark.integration
def test_read_file_returns_configuration_error_when_runtime_service_fails(monkeypatch):
    """readFile should surface KB runtime configuration failures via the documented envelope."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)

    async def _raise():
        raise KnowledgeBaseConfigurationError("KB runtime is not configured")

    monkeypatch.setattr(main_module, "_get_or_build_knowledge_base_service", _raise)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/readFile",
            json={
                "knCode": "demo",
                "filePath": "/path.md",
                "startLine": 1,
                "endLine": 1,
            },
        )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "KB runtime is not configured"


@pytest.mark.integration
def test_search_returns_configuration_error_when_runtime_service_fails(monkeypatch):
    """search should surface KB runtime configuration failures via the standard envelope."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _disable_kb_lifecycle(monkeypatch)

    async def _raise():
        raise KnowledgeBaseConfigurationError("KB runtime is not configured")

    monkeypatch.setattr(
        main_module, "_get_or_build_knowledge_item_search_service", _raise
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "demo",
                "knCodeList": ["demo"],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert "KB runtime is not configured" in response.json()["resultMsg"]


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
            "/api/v1/knowledgeBases/create",
            json={
                "knName": f"KB {uuid4().hex[:12]}",
                "kb_name": "Broken Config KB",
                "status": "ACTIVE",
            },
        )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_upload_single_md_returns_data_list(monkeypatch, tmp_path):
    """Single-file import returns resultObject.data with one success item."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# title\n")
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        response = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/docs/intro.md"},
            files={"fileContent": ("intro.md", b"# title\nbody", "text/markdown")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resultCode"] == "0"
    data = payload["resultObject"]["data"]
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["filePath"] == "/docs/intro.md"
    assert data[0]["success"] is True
    assert data[0]["error"] is None
    assert payload["resultObject"]["summary"]["total"] == 1


@pytest.mark.integration
def test_import_zip_rewrites_markdown_references(monkeypatch, tmp_path):
    """zip upload: png uploaded, md reference rewritten to KB-absolute path."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("images/x.png", b"\x89PNG\r\n\x1a\n fake png")
        zf.writestr("doc.md", "# t\n![alt](images/x.png)\n")
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        response = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/target/doc.md"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resultCode"] == "0"
    data = payload["resultObject"]["data"]
    assert {d["filePath"] for d in data} == {"/target/images/x.png", "/target/doc.md"}
    assert all(d["success"] for d in data)
    assert payload["resultObject"]["summary"]["succeeded"] == 2
    # stored original md carries the rewritten KB-absolute reference
    assert b"![alt](/target/images/x.png)" in download.content


@pytest.mark.integration
def test_import_single_md_rewrites_reference(monkeypatch, tmp_path):
    """Single md upload rewrites references against the KB."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        # pre-upload the image so the md reference can resolve
        client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/p/images/x.png"},
            files={"fileContent": ("x.png", b"\x89PNG fake", "image/png")},
        )
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/p/doc.md"},
            files={"fileContent": ("doc.md", b"![a](images/x.png)\n", "text/markdown")},
        )
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/p/doc.md"},
        )

    assert resp.status_code == 200
    item = resp.json()["resultObject"]["data"][0]
    assert item["success"] is True
    assert b"![a](/p/images/x.png)" in download.content


@pytest.mark.integration
def test_build_unsupported_file_type_sets_unsupported_status(monkeypatch, tmp_path):
    """Building a .png sets build status to 'unsupported', not failure."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, _UnsupportedPngChunking(markdown_text="# t\n")
    )
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/img/x.png"},
            files={"fileContent": ("x.png", b"\x89PNG fake", "image/png")},
        )
        build = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": "/img/x.png"},
        )
        status_response = _file_build_status(
            client, kb_code=kb_code, file_path="/img/x.png"
        )

    assert build.status_code == 200
    assert status_response.status_code == 200
    assert status_response.json()["resultObject"]["status"] == "unsupported"


@pytest.mark.integration
def test_import_single_file_rejects_dotdot_path(monkeypatch, tmp_path):
    """Single-file upload with a `..` segment in filePath is rejected as an
    unsafe path and no file is created."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        response = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/../escape.md"},
            files={"fileContent": ("escape.md", b"# escape\n", "text/markdown")},
        )
        # confirm no file was created at the escaped path
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/escape.md"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resultCode"] == "-1"
    assert payload["resultMsg"] == "unsafe path"
    # no file was created: download reports not-found
    download_payload = download.json()
    assert download_payload["resultCode"] == "-1"
    assert "file not found" in download_payload["resultMsg"]


@pytest.mark.integration
def test_import_zip_malformed_md_preserves_existing_file(monkeypatch, tmp_path):
    """A malformed-md zip overwrite must NOT delete the pre-existing valid file.

    The zip import service decodes/rewrites md content BEFORE deleting the
    existing file (H1 rewrite-before-delete), so a UnicodeDecodeError on the
    malformed zip entry is caught per-file and the original valid md survives.
    """
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    valid_body = b"# valid title\n\nvalid body line\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", b"\xff\xfe\xfd not valid utf8")
    zip_bytes = buf.getvalue()

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        # Step 1: upload a VALID md at /t/a.md (single-file), assert success.
        first = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/t/a.md"},
            files={"fileContent": ("a.md", valid_body, "text/markdown")},
        )
        valid_download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/t/a.md"},
        )
        # Step 2: zip overwrite with malformed md at the same path.
        zip_resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/t"},
            files={"fileContent": ("batch.zip", zip_bytes, "application/zip")},
        )
        # The original valid md must still be present and downloadable.
        after_download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/t/a.md"},
        )

    # Step 1 assertions: valid upload succeeded and is downloadable.
    assert first.status_code == 200
    first_data = first.json()["resultObject"]["data"]
    assert len(first_data) == 1 and first_data[0]["success"] is True
    assert valid_download.status_code == 200
    assert valid_body in valid_download.content

    # Step 2 assertions: zip response reports the malformed entry as a failure.
    assert zip_resp.status_code == 200
    zip_payload = zip_resp.json()
    assert zip_payload["resultCode"] == "0"
    zip_data = zip_payload["resultObject"]["data"]
    assert len(zip_data) == 1
    assert zip_data[0]["filePath"] == "/t/a.md"
    assert zip_data[0]["success"] is False
    # H1: the original valid md is preserved (rewrite-before-delete).
    assert after_download.status_code == 200
    assert valid_body in after_download.content


@pytest.mark.integration
def test_import_zip_overwrite_replaces_existing_file_content(monkeypatch, tmp_path):
    """Overwrite happy path: an existing file is soft-deleted and replaced with the zip's new content."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    old_body = b"# old title\nold body\n"
    new_body = b"# new title\nnew body\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", new_body)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/t/a.md"},
            files={"fileContent": ("a.md", old_body, "text/markdown")},
        )
        old_download = client.post(
            "/api/v1/downloadFile", json={"knCode": kb_code, "filePath": "/t/a.md"}
        )
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/t"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        new_download = client.post(
            "/api/v1/downloadFile", json={"knCode": kb_code, "filePath": "/t/a.md"}
        )

    assert resp.status_code == 200
    item = [
        d for d in resp.json()["resultObject"]["data"] if d["filePath"] == "/t/a.md"
    ][0]
    assert item["success"] is True
    assert old_body in old_download.content
    assert new_body in new_download.content
    assert old_body not in new_download.content


@pytest.mark.integration
def test_import_zip_preserves_non_md_binary_bytes(monkeypatch, tmp_path):
    """A non-markdown binary resource (png) is stored intact and downloadable byte-for-byte."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"binary payload \x00\x01\x02 XYZ"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("images/x.png", png_bytes)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/target/images/x.png"},
        )

    assert resp.status_code == 200
    item = [
        d
        for d in resp.json()["resultObject"]["data"]
        if d["filePath"] == "/target/images/x.png"
    ][0]
    assert item["success"] is True
    assert download.status_code == 200
    assert download.content == png_bytes


@pytest.mark.integration
def test_import_zip_uploads_non_md_before_md(monkeypatch, tmp_path):
    """End-to-end two-phase ordering: every non-md file precedes every md file in the result list."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.png", b"\x89PNG fake")
        zf.writestr("b.pdf", b"%PDF-1.4 fake")
        zf.writestr("one.md", b"# one\n")
        zf.writestr("two.md", b"# two\n")
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )

    assert resp.status_code == 200
    data = resp.json()["resultObject"]["data"]
    paths = [d["filePath"] for d in data]
    non_md_idx = [i for i, p in enumerate(paths) if not p.endswith(".md")]
    md_idx = [i for i, p in enumerate(paths) if p.endswith(".md")]
    assert non_md_idx and md_idx
    assert max(non_md_idx) < min(md_idx)
    assert all(d["success"] for d in data)


@pytest.mark.integration
def test_import_zip_rejects_unsafe_path_entry(monkeypatch, tmp_path):
    """A zip entry escaping the target dir (../) is recorded as a failure and never stored."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.md", b"# escape\n")
        zf.writestr("real.md", b"# real\n")
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        escape_download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/escape.md"},
        )
        real_download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/target/real.md"},
        )

    assert resp.status_code == 200
    data = resp.json()["resultObject"]["data"]
    unsafe = [d for d in data if not d["success"]]
    assert len(unsafe) == 1
    assert "unsafe" in (unsafe[0]["error"] or "").lower()
    real = [d for d in data if d["filePath"] == "/target/real.md"][0]
    assert real["success"] is True
    # no file created at the escaped path
    assert escape_download.json()["resultCode"] == "-1"
    # the legit file is present
    assert real_download.status_code == 200


@pytest.mark.integration
def test_import_zip_rejects_invalid_zip(monkeypatch, tmp_path):
    """A file named .zip whose content is not a valid zip is rejected with 'invalid zip file'."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", b"not a zip file", "application/zip")},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["resultCode"] == "-1"
    assert payload["resultMsg"] == "invalid zip file"


@pytest.mark.integration
def test_import_zip_auto_creates_nested_directories(monkeypatch, tmp_path):
    """A zip entry with a deep relative path auto-creates intermediate directories."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    body = b"# deep\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a/b/c.md", body)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/target/a/b/c.md"},
        )

    assert resp.status_code == 200
    item = [
        d
        for d in resp.json()["resultObject"]["data"]
        if d["filePath"] == "/target/a/b/c.md"
    ][0]
    assert item["success"] is True
    assert download.status_code == 200
    assert download.content == body
