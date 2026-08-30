"""User-journey oriented stateful integration tests for knowledge_base APIs."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings
from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest
from by_qa.knowledge_base.events import (
    KnowledgeEventPublisherInvoker,
    serialize_knowledge_event,
)
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.runtime import (
    build_knowledge_item_search_service,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError
from by_qa.knowledge_base.services.markdown_front_matter import split_front_matter
from by_qa.knowledge_build.services.document_chunking_service import (
    DocumentChunkingService,
)
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
        self.chunk_calls: list[dict[str, object]] = []

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
        self.chunk_calls.append({"file_bytes": file_bytes, "filename": filename})
        return [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=line_count,
                chunk_text=content.strip(),
                embedding=self.embedding,
            )
        ]


class EchoDocumentChunkingService(FakeDocumentChunkingService):
    """Chunking fake that keeps uploaded Markdown bytes visible to read/search."""

    def __init__(self, *, embedding: list[float] | None = None):
        super().__init__(markdown_text="", embedding=embedding)

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        self.extract_calls.append(
            {
                "file_bytes": file_bytes,
                "file_type": file_type,
            }
        )
        return file_bytes.decode("utf-8")


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


class LocalEmbeddingDocumentChunkingService(DocumentChunkingService):
    """Real splitter with deterministic local embeddings for integration tests."""

    def __init__(
        self,
        *,
        chunk_size: int,
        chunk_overlap: int = 0,
        embedding: list[float] | None = None,
    ):
        vector = embedding or _default_embedding_vector()
        super().__init__(
            embedding_base_url="https://embedding.example.com",
            embedding_api_key="secret",
            embedding_model_name="fake-embedding",
            embedding_dimension=len(vector),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self.embedding = vector

    def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [list(self.embedding) for _ in texts]


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
    monkeypatch.setattr(main_module, "_document_update_service", None)
    monkeypatch.setattr(main_module, "_metadata_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)
    monkeypatch.setattr(main_module, "_file_metadata_query_service", None)
    monkeypatch.setattr(main_module, "_file_metadata_update_service", None)
    monkeypatch.setattr(main_module, "_knowledge_event_publisher_invoker", None)
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
    metadata: dict | None = None,
) -> None:
    body = {
        "knCode": kb_code,
        "directoryPath": directory_path,
        "directoryDescription": f"{directory_path} description",
    }
    if metadata is not None:
        body["metadata"] = metadata
    response = client.post(
        "/api/v1/directories/create",
        json=body,
    )
    assert response.status_code == 200, response.text
    assert response.json()["resultCode"] == "0", response.json()


def _upload_file(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    file_content: bytes,
    content_type: str = "text/markdown",
    metadata: dict | None = None,
) -> None:
    """Upload a file via the multipart /api/v1/knowledgeItems/import endpoint."""
    data = {"knCode": kb_code, "filePath": file_path}
    if metadata is not None:
        data["metadata"] = json.dumps(metadata, ensure_ascii=False)
    response = client.post(
        "/api/v1/knowledgeItems/import",
        data=data,
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


def _read_file_data(client: TestClient, *, kb_code: str, file_path: str) -> str:
    response = client.post(
        "/api/v1/readFile",
        json={"knCode": kb_code, "filePath": file_path},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["data"]


def _read_file_window_data(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str:
    response = client.post(
        "/api/v1/readFile",
        json={
            "knCode": kb_code,
            "filePath": file_path,
            "startLine": start_line,
            "endLine": end_line,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["data"]


def _download_file_bytes(client: TestClient, *, kb_code: str, file_path: str) -> bytes:
    response = client.post(
        "/api/v1/downloadFile",
        json={"knCode": kb_code, "filePath": file_path},
    )
    assert response.status_code == 200, response.text
    return response.content


def _get_file_metadata(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    field_names: list[str],
) -> dict:
    response = client.post(
        "/api/v1/knowledgeItems/metadata/get",
        json={
            "knCode": kb_code,
            "filePath": file_path,
            "metadataFieldList": field_names,
        },
    )
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["metadata"]


def _update_file_metadata(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    operation_list: list[dict],
):
    return client.post(
        "/api/v1/knowledgeItems/metadata/update",
        json={
            "knCode": kb_code,
            "filePath": file_path,
            "operationList": operation_list,
        },
    )


async def _metadata_row_counts(settings: Settings, *, kb_code: str) -> tuple[int, int]:
    connection = await build_connection_factory(settings)()
    try:
        cursor = connection.cursor()
        await cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE is_deleted = false) AS active_count,
                COUNT(*) FILTER (WHERE is_deleted = true) AS deleted_count
            FROM knowledge_file_metadata_value
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            {"knowledge_base_id": int(kb_code)},
        )
        row = await cursor.fetchone()
        return int(row["active_count"]), int(row["deleted_count"])
    finally:
        await connection.close()


def _update_file(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    file_content: bytes,
    upload_name: str | None = None,
    content_type: str = "text/markdown",
    process_front_matter: bool = True,
    file_description: str | None = None,
    include_file_description: bool = False,
    metadata: dict | None = None,
    refer_signature: str | None = None,
) -> object:
    """Replace one existing file through the public multipart update endpoint."""
    data = {
        "knCode": kb_code,
        "filePath": file_path,
        "processFrontMatter": str(process_front_matter).lower(),
    }
    if include_file_description:
        data["fileDescription"] = file_description or ""
    if metadata is not None:
        data["metadata"] = json.dumps(metadata, ensure_ascii=False)
    if refer_signature is not None:
        data["referSignature"] = refer_signature
    return client.post(
        "/api/v1/knowledgeItems/update",
        data=data,
        files={
            "fileContent": (
                upload_name or file_path.rsplit("/", 1)[-1],
                file_content,
                content_type,
            )
        },
    )


@pytest.mark.integration
def test_resource_mutation_events_cover_sync_file_and_directory_lifecycle(
    monkeypatch, tmp_path
):
    """Every committed synchronous tree mutation publishes one detached event."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    connection_factory = build_connection_factory(settings)

    class CommitObservingPublisher:
        def __init__(self):
            self.events = []
            self.committed_observations = []

        async def publish(self, event):
            payload = event.payload.model_dump(by_alias=True)
            path = payload.get("targetPath") or payload.get("sourcePath")
            connection = await connection_factory()
            try:
                cursor = connection.cursor()
                await cursor.execute(
                    """
                    SELECT is_deleted
                    FROM knowledge_fs_entry
                    WHERE knowledge_base_id = %(knowledge_base_id)s
                      AND virtual_path = %(virtual_path)s
                    ORDER BY kid DESC
                    LIMIT 1
                    """,
                    {
                        "knowledge_base_id": int(event.kb_code),
                        "virtual_path": path,
                    },
                )
                row = await cursor.fetchone()
            finally:
                await connection.close()
            expected_deleted = event.event_type.endswith(".deleted")
            self.committed_observations.append(
                row is not None and bool(row["is_deleted"]) is expected_deleted
            )
            self.events.append(event)

    publisher = CommitObservingPublisher()
    main_module._knowledge_event_publisher_invoker = KnowledgeEventPublisherInvoker(
        publisher
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Event KB {uuid4().hex[:12]}")

        create_directory = client.post(
            "/api/v1/directories/create",
            json={
                "knCode": kb_code,
                "directoryPath": "/docs",
            },
        )
        assert create_directory.json()["resultCode"] == "0"

        client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/docs",
                "directoryName": "renamed",
            },
        )
        client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/renamed/a.md",
            },
            files={"fileContent": ("a.md", b"# Before\n", "text/markdown")},
        )
        client.post(
            "/api/v1/knowledgeItems/update",
            data={
                "knCode": kb_code,
                "filePath": "/renamed/a.md",
            },
            files={"fileContent": ("a.md", b"# After\n", "text/markdown")},
        )
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/archive"},
        )
        client.post(
            "/api/v1/knowledgeItems/move",
            json={
                "knCode": kb_code,
                "sourcePath": ["/renamed/a.md"],
                "targetDirectoryPath": "/archive",
            },
        )
        client.post(
            "/api/v1/knowledgeItems/delete",
            json={
                "knCode": kb_code,
                "filePath": "/archive/a.md",
            },
        )
        client.post(
            "/api/v1/directories/delete",
            json={
                "knCode": kb_code,
                "directoryPath": "/renamed",
            },
        )

    assert [event.event_type for event in publisher.events] == [
        "resource.directory.created",
        "resource.directory.updated",
        "resource.file.imported",
        "resource.file.updated",
        "resource.directory.created",
        "resource.moved",
        "resource.file.deleted",
        "resource.directory.deleted",
    ]
    assert all(publisher.committed_observations)
    assert all(
        "extraParams" not in event.model_dump(by_alias=True)
        for event in publisher.events
    )
    assert all(
        "resourceId" not in event.payload.model_dump(by_alias=True)
        for event in publisher.events
    )


@pytest.mark.integration
def test_async_file_build_publishes_strict_event_after_terminal_commit(
    monkeypatch, tmp_path
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Built\n"),
    )
    connection_factory = build_connection_factory(settings)

    class CommitObservingPublisher:
        def __init__(self):
            self.events = []
            self.serialized_events = []
            self.persisted_statuses = []

        async def publish(self, event):
            self.events.append(event)
            self.serialized_events.append(serialize_knowledge_event(event))
            if event.event_type != "build.file.completed":
                return
            connection = await connection_factory()
            try:
                cursor = connection.cursor()
                await cursor.execute(
                    "SELECT status FROM knowledge_build_task WHERE kid = %(task_id)s",
                    {"task_id": int(event.payload.task_id)},
                )
                row = await cursor.fetchone()
            finally:
                await connection.close()
            self.persisted_statuses.append(row["status"] if row else None)

    publisher = CommitObservingPublisher()
    main_module._knowledge_event_publisher_invoker = KnowledgeEventPublisherInvoker(
        publisher
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Build Event KB {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"# Source\n",
        )

    build_events = [
        event
        for event in publisher.events
        if event.event_type == "build.file.completed"
    ]
    assert len(build_events) == 1
    event = build_events[0]
    assert event.kb_code == kb_code
    assert event.payload.status == "complete"
    assert event.payload.file_path == "docs/a.md"
    assert event.payload.current_step == "complete"
    assert event.payload.result.chunk_count == 1
    assert event.payload.error is None
    assert publisher.persisted_statuses == ["complete"]
    serialized = next(
        item
        for item in publisher.serialized_events
        if item["eventType"] == "build.file.completed"
    )
    assert set(serialized) == {
        "eventId",
        "eventVersion",
        "eventType",
        "knCode",
        "occurredAt",
        "payload",
    }
    assert serialized["payload"] == {
        "taskId": event.payload.task_id,
        "status": "complete",
        "filePath": "docs/a.md",
        "currentStep": "complete",
        "result": {"chunkCount": 1, "lineCount": 2},
        "error": None,
    }
    assert "extraParams" not in event.model_dump(by_alias=True)
    assert "resourceId" not in event.payload.model_dump(by_alias=True)


@pytest.mark.integration
def test_async_file_build_publishes_unsupported_terminal_event(monkeypatch, tmp_path):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        _UnsupportedPngChunking(markdown_text="unused"),
    )

    class RecordingPublisher:
        def __init__(self):
            self.events = []

        async def publish(self, event):
            self.events.append(event)

    publisher = RecordingPublisher()
    main_module._knowledge_event_publisher_invoker = KnowledgeEventPublisherInvoker(
        publisher
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Unsupported Build KB {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/images/a.png",
            file_content=b"\x89PNG fake",
            content_type="image/png",
        )
        response = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": "/images/a.png"},
        )
        status = _file_build_status(
            client,
            kb_code=kb_code,
            file_path="/images/a.png",
        )

    assert response.json()["resultCode"] == "0"
    assert status.json()["resultObject"]["status"] == "unsupported"
    build_events = [
        event
        for event in publisher.events
        if event.event_type == "build.file.completed"
    ]
    assert len(build_events) == 1
    event = build_events[0]
    assert event.payload.status == "unsupported"
    assert event.payload.current_step == "markdown"
    assert event.payload.result is None
    assert event.payload.error.code == "UNSUPPORTED_FILE_TYPE"
    assert event.payload.error.message == "unsupported file type: png"


@pytest.mark.integration
@pytest.mark.parametrize("publisher_mode", ["raise", "timeout"])
def test_async_build_publisher_failure_does_not_change_committed_status(
    monkeypatch, tmp_path, publisher_mode
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Built\n"),
    )

    class UnavailablePublisher:
        async def publish(self, event):
            del event
            if publisher_mode == "timeout":
                await asyncio.sleep(0.05)
            raise RuntimeError("publisher unavailable")

    main_module._knowledge_event_publisher_invoker = KnowledgeEventPublisherInvoker(
        UnavailablePublisher(), timeout_seconds=0.001
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Publisher Failure KB {uuid4().hex[:12]}")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"# Source\n",
        )
        status = _file_build_status(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
        )

    assert status.json()["resultObject"]["status"] == "complete"
    assert status.json()["resultObject"]["currentStep"] == "complete"


@pytest.mark.integration
def test_partial_move_publishes_results_and_all_failure_publishes_nothing(
    monkeypatch, tmp_path
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    class RecordingPublisher:
        def __init__(self):
            self.events = []

        async def publish(self, event):
            self.events.append(event)

    publisher = RecordingPublisher()
    main_module._knowledge_event_publisher_invoker = KnowledgeEventPublisherInvoker(
        publisher
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Partial Move KB {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/archive")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/a.md",
            file_content=b"# A\n",
        )
        publisher.events.clear()

        partial = client.post(
            "/api/v1/knowledgeItems/move",
            json={
                "knCode": kb_code,
                "sourcePath": ["/a.md", "/missing.md"],
                "targetDirectoryPath": "/archive",
            },
        )
        event_count_after_partial = len(publisher.events)
        all_failed = client.post(
            "/api/v1/knowledgeItems/move",
            json={
                "knCode": kb_code,
                "sourcePath": ["/still-missing.md"],
                "targetDirectoryPath": "/archive",
            },
        )

    partial_result = partial.json()["resultObject"]
    assert partial_result["summary"] == {"total": 2, "succeeded": 1, "failed": 1}
    move_events = [
        event for event in publisher.events if event.event_type == "resource.moved"
    ]
    assert len(move_events) == 1
    assert move_events[0].payload.result.model_dump() == {
        "total": 2,
        "succeeded": 1,
        "failed": 1,
    }
    assert [item.success for item in move_events[0].payload.items] == [True, False]
    assert event_count_after_partial == 1
    assert len(publisher.events) == event_count_after_partial
    assert all_failed.json()["resultObject"]["summary"] == {
        "total": 1,
        "succeeded": 0,
        "failed": 1,
    }


@pytest.mark.integration
async def test_document_update_can_skip_front_matter_and_preserve_existing_metadata(
    monkeypatch, tmp_path
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Update KB {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"---\ntitle: Before\n---\n# Old\n",
        )
        response = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"---\ntitle: After\n---\n# New\n",
            process_front_matter=False,
        )
        raw_bytes = _download_file_bytes(
            client, kb_code=kb_code, file_path="/docs/a.md"
        )
        metadata = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": "/docs/a.md",
                "metadataFieldList": ["title"],
            },
        )
    assert response.json()["resultCode"] == "0"
    assert raw_bytes.startswith(b"---\ntitle: Before")
    assert raw_bytes.endswith(b"# New\n")
    assert metadata.json()["resultObject"]["metadata"]["title"]["value"] == "Before"


@pytest.mark.integration
def test_document_update_metadata_merges_preserves_and_rolls_back_with_content(
    monkeypatch, tmp_path
):
    """Update metadata shares content transaction and front matter wins conflicts."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Built\n"),
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Update metadata {uuid4().hex[:12]}")
        imported = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/docs/a.md",
                "metadata": '{"preserved":"keep","owner":"old"}',
            },
            files={"fileContent": ("a.md", b"# Old\n", "text/markdown")},
        )
        assert imported.json()["resultCode"] == "0"
        built = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": "/docs/a.md"},
        )
        assert built.json()["resultCode"] == "0"
        before_update_browse = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        ).json()["resultObject"]["data"][0]

        updated = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"---\nowner: front-matter\nfrontOnly: 8\n---\n# New\n",
            metadata={"owner": "request", "requestOnly": True},
        )
        after_update = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            field_names=["preserved", "owner", "requestOnly", "frontOnly"],
        )
        browse = client.post(
            "/api/v1/listDir",
            json={
                "knCode": kb_code,
                "directoryPath": "/docs",
                "metadataFieldList": [
                    "preserved",
                    "owner",
                    "requestOnly",
                    "frontOnly",
                ],
            },
        ).json()["resultObject"]["data"][0]
        glob_browse = client.post(
            "/api/v1/glob",
            json={
                "knCode": kb_code,
                "pathRule": "/docs/*",
                "metadataFieldList": [
                    "preserved",
                    "owner",
                    "requestOnly",
                    "frontOnly",
                ],
            },
        ).json()["resultObject"]["data"][0]

        stale = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"# Must not persist\n",
            metadata={"owner": "must-not-persist"},
            refer_signature="stale-signature",
        )
        after_stale = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            field_names=["preserved", "owner", "requestOnly", "frontOnly"],
        )

        disabled = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"---\nowner: ignored-front\n---\n# Final\n",
            process_front_matter=False,
            metadata={"owner": "explicit-final"},
        )
        final_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            field_names=["preserved", "owner", "requestOnly", "frontOnly"],
        )

    expected = {
        "preserved": {"valueType": "string", "value": "keep"},
        "owner": {"valueType": "string", "value": "front-matter"},
        "requestOnly": {"valueType": "boolean", "value": True},
        "frontOnly": {"valueType": "number", "value": 8.0},
    }
    assert updated.json()["resultCode"] == "0"
    assert after_update == expected
    assert browse["metadata"] == expected
    assert glob_browse == browse
    assert browse["updatedAt"] > before_update_browse["updatedAt"]
    assert browse["buildStatus"] is None
    assert browse["buildCurrentStep"] is None
    assert stale.json()["resultCode"] == "-1"
    assert after_stale == expected
    assert disabled.json()["resultCode"] == "0"
    assert final_metadata == {
        **expected,
        "owner": {"valueType": "string", "value": "explicit-final"},
    }


@pytest.mark.integration
def test_document_update_failures_preserve_content_and_metadata(monkeypatch, tmp_path):
    """Duplicate, storage, and metadata failures never leave a partial update."""
    from by_qa.knowledge_base.infrastructure.storage_s3 import (
        S3KnowledgeStorageProvider,
    )
    from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
        FileMetadataValueRepository,
    )

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Update rollback {uuid4().hex[:12]}")
        for file_path, content, metadata in (
            ("/docs/source.md", b"# Original\n", '{"status":"original"}'),
            ("/docs/duplicate.md", b"# Duplicate\n", None),
        ):
            data = {"knCode": kb_code, "filePath": file_path}
            if metadata is not None:
                data["metadata"] = metadata
            response = client.post(
                "/api/v1/knowledgeItems/import",
                data=data,
                files={
                    "fileContent": (
                        file_path.rsplit("/", 1)[-1],
                        content,
                        "text/markdown",
                    )
                },
            )
            assert response.json()["resultCode"] == "0", response.json()

        duplicate_failure = client.post(
            "/api/v1/knowledgeItems/update",
            data={
                "knCode": kb_code,
                "filePath": "/docs/source.md",
                "skipIfDuplicate": "true",
                "metadata": '{"status":"duplicate-rejected"}',
            },
            files={
                "fileContent": (
                    "source.md",
                    b"# Duplicate\n",
                    "text/markdown",
                )
            },
        ).json()
        original_write = S3KnowledgeStorageProvider.write

        async def fail_storage(self, *args, **kwargs):
            del self, args, kwargs
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(S3KnowledgeStorageProvider, "write", fail_storage)
        storage_failure = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/source.md",
            file_content=b"# Storage rejected\n",
            metadata={"status": "storage-rejected"},
        ).json()
        monkeypatch.setattr(S3KnowledgeStorageProvider, "write", original_write)

        original_upsert = FileMetadataValueRepository.upsert_value

        async def fail_status(self, cursor, **kwargs):
            if kwargs["property_name"] == "status":
                raise RuntimeError("simulated metadata database failure")
            return await original_upsert(
                self,
                cursor,
                fs_entry_id=kwargs["fs_entry_id"],
                knowledge_base_id=kwargs["knowledge_base_id"],
                property_name=kwargs["property_name"],
                value_type=kwargs["value_type"],
                value=kwargs["value"],
            )

        monkeypatch.setattr(FileMetadataValueRepository, "upsert_value", fail_status)
        database_failure = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/source.md",
            file_content=b"# Database rejected\n",
            metadata={"status": "database-rejected"},
        ).json()
        metadata_after_failures = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs/source.md",
            field_names=["status"],
        )
        content_after_failures = _download_file_bytes(
            client,
            kb_code=kb_code,
            file_path="/docs/source.md",
        )

    for failure in (duplicate_failure, storage_failure, database_failure):
        assert failure["resultCode"] == "-1"
    assert metadata_after_failures == {
        "status": {"valueType": "string", "value": "original"}
    }
    assert b"# Original" in content_after_failures
    assert b"rejected" not in content_after_failures.lower()


@pytest.mark.integration
def test_document_update_rejects_invalid_paths_and_missing_multipart_fields(
    monkeypatch, tmp_path
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Update KB {uuid4().hex[:12]}")
        _upload_file(
            client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Old\n"
        )
        responses = [
            _update_file(
                client, kb_code=kb_code, file_path="/", file_content=b"# New\n"
            ),
            _update_file(
                client,
                kb_code=kb_code,
                file_path="/docs/./a.md",
                file_content=b"# New\n",
            ),
            _update_file(
                client,
                kb_code=kb_code,
                file_path="/docs/../a.md",
                file_content=b"# New\n",
            ),
            client.post(
                "/api/v1/knowledgeItems/update",
                data={"knCode": kb_code, "filePath": "/docs/a.md"},
            ),
        ]
    for response in responses:
        assert response.status_code == 200
        assert response.json()["resultCode"] == "-1"


@pytest.mark.integration
async def test_document_update_file_description_preserves_clears_and_replaces(
    monkeypatch, tmp_path
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(MarkdownUpdateSummaryService, "generate_llm_summary", no_llm)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Update KB {uuid4().hex[:12]}")
        _upload_file(
            client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Old\n"
        )
        _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"# One\n",
            file_description="first",
            include_file_description=True,
        )
    assert (
        await _file_description(settings, kb_code=kb_code, file_path="/docs/a.md")
        == "first"
    )
    with TestClient(main_module.app) as client:
        _update_file(
            client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Two\n"
        )
    assert (
        await _file_description(settings, kb_code=kb_code, file_path="/docs/a.md")
        == "first"
    )
    with TestClient(main_module.app) as client:
        _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"# Three\n",
            file_description="",
            include_file_description=True,
        )
    assert (
        await _file_description(settings, kb_code=kb_code, file_path="/docs/a.md") == ""
    )


@pytest.mark.integration
async def test_document_update_serializes_concurrent_updates_for_one_file(
    monkeypatch, tmp_path
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(MarkdownUpdateSummaryService, "generate_llm_summary", no_llm)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Update KB {uuid4().hex[:12]}")
        _upload_file(
            client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Old\n"
        )

    def update(content: bytes):
        with TestClient(main_module.app) as client:
            return _update_file(
                client, kb_code=kb_code, file_path="/docs/a.md", file_content=content
            ).json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, [b"# One\n", b"# Two\n"]))
    assert [item["resultCode"] for item in results] == ["0", "0"]
    assert (
        await _update_timeline_count(settings, kb_code=kb_code, file_path="/docs/a.md")
        == 2
    )


async def _latest_update_timeline(
    settings: Settings, *, kb_code: str, file_path: str
) -> dict:
    """Read the persisted update event for a document after its HTTP request completes."""

    async def _read() -> dict:
        connection = await build_connection_factory(settings)()
        try:
            cursor = connection.cursor()
            entry = await KnowledgeFsEntryRepository().get_file_by_path(
                cursor,
                knowledge_base_id=int(kb_code),
                full_path=file_path.strip("/"),
            )
            assert entry is not None
            await cursor.execute(
                """
                SELECT timeline.summary, timeline.summary_source,
                       timeline.old_file_size, timeline.new_file_size
                FROM knowledge_file_update_timeline AS timeline
                WHERE timeline.fs_entry_id = %(fs_entry_id)s
                ORDER BY timeline.kid DESC
                LIMIT 1
                """,
                {"fs_entry_id": entry["kid"]},
            )
            row = await cursor.fetchone()
            assert row is not None
            return row
        finally:
            await connection.close()

    return await _read()


async def _file_description(settings: Settings, *, kb_code: str, file_path: str):
    connection = await build_connection_factory(settings)()
    try:
        cursor = connection.cursor()
        entry = await KnowledgeFsEntryRepository().get_file_by_path(
            cursor, knowledge_base_id=int(kb_code), full_path=file_path.strip("/")
        )
        assert entry is not None
        return entry["description"]
    finally:
        await connection.close()


async def _update_timeline_count(
    settings: Settings, *, kb_code: str, file_path: str
) -> int:
    connection = await build_connection_factory(settings)()
    try:
        cursor = connection.cursor()
        entry = await KnowledgeFsEntryRepository().get_file_by_path(
            cursor, knowledge_base_id=int(kb_code), full_path=file_path.strip("/")
        )
        assert entry is not None
        await cursor.execute(
            "SELECT COUNT(*) AS count FROM knowledge_file_update_timeline WHERE fs_entry_id = %(fs_entry_id)s",
            {"fs_entry_id": entry["kid"]},
        )
        return int((await cursor.fetchone())["count"])
    finally:
        await connection.close()


async def _create_running_build_task(
    settings: Settings, *, kb_code: str, name: str
) -> None:
    """Seed a running task to verify updates reject concurrent builds at the HTTP boundary."""
    connection = await build_connection_factory(settings)()
    try:
        cursor = connection.cursor()
        await cursor.execute(
            """
            INSERT INTO knowledge_build_task (
                knowledge_base_id, fs_entry_id, status, current_step, started_at
            )
            SELECT %(kb_code)s::bigint, kid, 'running', 'extract_text', NOW()
            FROM knowledge_fs_entry
            WHERE knowledge_base_id = %(kb_code)s::bigint
              AND name = %(name)s
              AND entry_type = 'FILE'
              AND is_deleted = false
            """,
            {"kb_code": kb_code, "name": name},
        )
        await connection.commit()
    finally:
        await connection.close()


def _search_items(
    client: TestClient,
    *,
    kb_code: str,
    query: str,
) -> list[dict]:
    response = client.post(
        "/api/v1/knowledgeItems/search",
        json={
            "query": query,
            "knCodeList": [kb_code],
            "topK": 10,
            "searchMode": "mixedRecall",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["data"]


def _search_chunk_texts(
    client: TestClient,
    *,
    kb_code: str,
    query: str,
) -> list[str]:
    return [
        item["chunkText"]
        for item in _search_items(client, kb_code=kb_code, query=query)
    ]


def _reference_rows(
    client: TestClient,
    *,
    kb_code: str,
    target_path: str,
) -> list[dict]:
    response = client.post(
        "/api/v1/knowledgeItems/references",
        json={"knCode": kb_code, "filePath": target_path},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["inbound"]


def _reference_result(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    direction: str,
) -> dict:
    response = client.post(
        "/api/v1/knowledgeItems/references",
        json={"knCode": kb_code, "filePath": file_path, "direction": direction},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]


def _move_items(
    client: TestClient,
    *,
    kb_code: str,
    source_path: list[str],
    target_directory_path: str | None = None,
    target_file_path: str | None = None,
    metadata: dict | None = None,
) -> list[dict]:
    body = {"knCode": kb_code, "sourcePath": source_path}
    if target_directory_path is not None:
        body["targetDirectoryPath"] = target_directory_path
    if target_file_path is not None:
        body["targetFilePath"] = target_file_path
    if metadata is not None:
        body["metadata"] = metadata
    response = client.post("/api/v1/knowledgeItems/move", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    data = payload["resultObject"]["data"]
    assert all(item["success"] for item in data), payload
    return data


def _list_dir_entry_names(response) -> set[str]:
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return {item["name"] for item in payload["resultObject"]["data"]}


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
\xe4\xbc\x9a\xe8\xae\xae\xe6\x97\xa5\xe6\x9c\x9f: "2026-05-25"
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
def test_import_metadata_merges_with_front_matter_and_can_disable_it(monkeypatch):
    """Explicit import metadata is independent from front-matter processing."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    markdown = b"""---
owner: front-matter
frontOnly: 7
---
# Body
"""

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Import metadata {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/existing",
            metadata={"owner": "existing"},
        )
        merged_response = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/existing/auto/merged.md",
                "metadata": (
                    '{"owner":"request","requestOnly":true,"tags":["a","b"],'
                    '"publishedAt":"2026-08-30T01:02:03Z"}'
                ),
            },
            files={"fileContent": ("merged.md", markdown, "text/markdown")},
        )
        disabled_response = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/disabled.md",
                "processFrontMatter": "false",
                "metadata": '{"owner":"request","requestOnly":true}',
            },
            files={"fileContent": ("disabled.md", markdown, "text/markdown")},
        )
        binary_response = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/manual.pdf",
                "metadata": '{"owner":"PDF","priority":2.5}',
            },
            files={"fileContent": ("manual.pdf", b"pdf", "application/pdf")},
        )
        invalid_response = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/invalid.md",
                "metadata": "not-json",
            },
            files={"fileContent": ("invalid.md", b"# Invalid", "text/markdown")},
        )
        non_object_responses = [
            client.post(
                "/api/v1/knowledgeItems/import",
                data={
                    "knCode": kb_code,
                    "filePath": f"/non-object-{index}.md",
                    "metadata": metadata,
                },
                files={
                    "fileContent": (
                        f"non-object-{index}.md",
                        b"# Invalid",
                        "text/markdown",
                    )
                },
            )
            for index, metadata in enumerate(("[]", '"scalar"', "1"), start=1)
        ]
        merged = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/existing/auto/merged.md",
            field_names=[
                "owner",
                "requestOnly",
                "frontOnly",
                "tags",
                "publishedAt",
            ],
        )
        auto_parent = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/existing/auto",
            field_names=[
                "owner",
                "requestOnly",
                "frontOnly",
                "tags",
                "publishedAt",
            ],
        )
        existing_parent = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/existing",
            field_names=["owner", "requestOnly", "frontOnly"],
        )
        disabled = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/disabled.md",
            field_names=["owner", "requestOnly", "frontOnly"],
        )
        binary = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/manual.pdf",
            field_names=["owner", "priority"],
        )
        invalid_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        ).json()["resultObject"]["data"]

    assert merged_response.json()["resultCode"] == "0"
    assert disabled_response.json()["resultCode"] == "0"
    assert binary_response.json()["resultCode"] == "0"
    assert invalid_response.json()["resultCode"] == "-1"
    assert all(
        response.json()["resultCode"] == "-1" for response in non_object_responses
    )
    assert merged == {
        "owner": {"valueType": "string", "value": "front-matter"},
        "requestOnly": {"valueType": "boolean", "value": True},
        "frontOnly": {"valueType": "number", "value": 7.0},
        "tags": {"valueType": "stringList", "value": ["a", "b"]},
        "publishedAt": {
            "valueType": "datetime",
            "value": "2026-08-30T09:02:03+08:00",
        },
    }
    assert auto_parent == {
        "owner": {"valueType": "string", "value": "request"},
        "requestOnly": {"valueType": "boolean", "value": True},
        "tags": {"valueType": "stringList", "value": ["a", "b"]},
        "publishedAt": {
            "valueType": "datetime",
            "value": "2026-08-30T09:02:03+08:00",
        },
    }
    assert existing_parent == {"owner": {"valueType": "string", "value": "existing"}}
    assert disabled == {
        "owner": {"valueType": "string", "value": "request"},
        "requestOnly": {"valueType": "boolean", "value": True},
    }
    assert binary == {
        "owner": {"valueType": "string", "value": "PDF"},
        "priority": {"valueType": "number", "value": 2.5},
    }
    assert "/invalid.md" not in {item["name"] for item in invalid_list}
    assert not any(item["name"].startswith("/non-object-") for item in invalid_list)


@pytest.mark.integration
def test_import_metadata_failure_rolls_back_entry_storage_and_values(
    monkeypatch, tmp_path
):
    """A metadata repository failure compensates storage and rolls back the entry."""
    from by_qa.knowledge_base.infrastructure.storage_s3 import (
        S3KnowledgeStorageProvider,
    )
    from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
        FileMetadataValueRepository,
    )

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Import metadata rollback {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/seed.md",
            file_content=b"# Seed\n",
        )
        original_upsert = FileMetadataValueRepository.upsert_value
        original_delete = S3KnowledgeStorageProvider.delete_quietly
        compensated_locations = []

        async def record_delete(self, location):
            compensated_locations.append(location)
            return await original_delete(self, location)

        async def fail_owner(self, cursor, **kwargs):
            if kwargs["property_name"] == "owner":
                raise RuntimeError("simulated metadata write failure")
            return await original_upsert(
                self,
                cursor,
                fs_entry_id=kwargs["fs_entry_id"],
                knowledge_base_id=kwargs["knowledge_base_id"],
                property_name=kwargs["property_name"],
                value_type=kwargs["value_type"],
                value=kwargs["value"],
            )

        monkeypatch.setattr(FileMetadataValueRepository, "upsert_value", fail_owner)
        monkeypatch.setattr(S3KnowledgeStorageProvider, "delete_quietly", record_delete)
        failed = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/rollback.md",
                "metadata": '{"owner":"Alice"}',
            },
            files={"fileContent": ("rollback.md", b"# Rollback\n", "text/markdown")},
        ).json()
        listed = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        ).json()["resultObject"]["data"]

    assert failed["resultCode"] == "-1"
    assert "/rollback.md" not in {item["name"] for item in listed}
    assert len(compensated_locations) == 1


@pytest.mark.integration
def test_entry_metadata_replaces_changed_types_and_rejects_system_fields(
    monkeypatch, tmp_path
):
    """All direct metadata writers share one value and read-only-field contract."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata contract {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/typed",
            metadata={"directoryValue": 1},
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/typed",
            metadata={"directoryValue": "ready"},
        )
        imported = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/typed/file.md",
                "metadata": '{"fileValue":1}',
            },
            files={"fileContent": ("file.md", b"# Old\n", "text/markdown")},
        )
        assert imported.json()["resultCode"] == "0", imported.json()
        updated = _update_file(
            client,
            kb_code=kb_code,
            file_path="/typed/file.md",
            file_content=b"# Updated\n",
            metadata={"fileValue": "ready"},
        )
        assert updated.json()["resultCode"] == "0", updated.json()

        directory_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/typed",
            field_names=["directoryValue"],
        )
        file_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/typed/file.md",
            field_names=["fileValue"],
        )
        stale_type_searches = [
            client.post(
                "/api/v1/knowledgeItems/metadataSearch",
                json={
                    "knCodeList": [kb_code],
                    "where": {"eq": {"fieldName": field_name, "value": 1}},
                },
            ).json()["resultObject"]
            for field_name in ("directoryValue", "fileValue")
        ]
        current_type_searches = [
            client.post(
                "/api/v1/knowledgeItems/metadataSearch",
                json={
                    "knCodeList": [kb_code],
                    "where": {"eq": {"fieldName": field_name, "value": "ready"}},
                },
            ).json()["resultObject"]
            for field_name in ("directoryValue", "fileValue")
        ]

        rejected_import = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/forbidden.md",
                "metadata": '{"fileName":"forged.md"}',
            },
            files={"fileContent": ("forbidden.md", b"# Forbidden\n", "text/markdown")},
        ).json()
        rejected_create = client.post(
            "/api/v1/directories/create",
            json={
                "knCode": kb_code,
                "directoryPath": "/forbidden",
                "metadata": {"filePath": "/forged"},
            },
        ).json()
        rejected_update = _update_file(
            client,
            kb_code=kb_code,
            file_path="/typed/file.md",
            file_content=b"# Rejected\n",
            metadata={"updatedAt": "2026-08-30T00:00:00Z"},
        ).json()
        rejected_rename = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/typed",
                "directoryName": "renamed",
                "metadata": {"fileSize": 999},
            },
        ).json()
        root_items = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        ).json()["resultObject"]["data"]
        typed_items = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/typed"},
        ).json()["resultObject"]["data"]
        downloaded = _download_file_bytes(
            client,
            kb_code=kb_code,
            file_path="/typed/file.md",
        )

    assert directory_metadata == {
        "directoryValue": {"valueType": "string", "value": "ready"}
    }
    assert file_metadata == {"fileValue": {"valueType": "string", "value": "ready"}}
    assert [result["total"] for result in stale_type_searches] == [0, 0]
    assert [result["total"] for result in current_type_searches] == [1, 1]
    for rejected in (
        rejected_import,
        rejected_create,
        rejected_update,
        rejected_rename,
    ):
        assert rejected["resultCode"] == "-1"
        assert "metadata field is read-only" in rejected["resultMsg"]
    assert {item["name"] for item in root_items} == {"/typed"}
    assert {item["name"] for item in typed_items} == {"/typed/file.md"}
    assert b"# Updated" in downloaded
    assert b"# Rejected" not in downloaded


@pytest.mark.integration
def test_zip_import_applies_common_metadata_before_each_markdown_front_matter(
    monkeypatch,
):
    """Zip metadata is common to every entry and each front matter wins conflicts."""
    import io
    import zipfile

    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("plain.txt", "plain")
        output.writestr(
            "note.md",
            "---\nowner: note-owner\nnoteOnly: true\n---\n# Note\n",
        )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Zip metadata {uuid4().hex[:12]}")
        response = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/zip-meta",
                "metadata": '{"owner":"common","commonOnly":"all"}',
            },
            files={
                "fileContent": (
                    "metadata.zip",
                    archive.getvalue(),
                    "application/zip",
                )
            },
        )
        plain = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/zip-meta/plain.txt",
            field_names=["owner", "commonOnly", "noteOnly"],
        )
        note = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/zip-meta/note.md",
            field_names=["owner", "commonOnly", "noteOnly"],
        )

    assert response.json()["resultObject"]["summary"] == {
        "total": 2,
        "succeeded": 2,
        "failed": 0,
    }
    assert plain == {
        "owner": {"valueType": "string", "value": "common"},
        "commonOnly": {"valueType": "string", "value": "all"},
    }
    assert note == {
        "owner": {"valueType": "string", "value": "note-owner"},
        "commonOnly": {"valueType": "string", "value": "all"},
        "noteOnly": {"valueType": "boolean", "value": True},
    }


@pytest.mark.integration
def test_metadata_update_is_atomic_and_downloads_current_front_matter(monkeypatch):
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# meeting\n"),
    )
    markdown = b"""---
status: draft
tags:
  - existing
  - contract
owner: Alice
---

# Meeting
Body
"""

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata update {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/meeting.md",
            file_content=markdown,
        )

        response = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": "/meeting.md",
                "operationList": [
                    {
                        "propertyName": "status",
                        "operation": "set",
                        "valueType": "string",
                        "value": "active",
                    },
                    {
                        "propertyName": "tags",
                        "operation": "append",
                        "value": ["contract", "renewal"],
                    },
                    {"propertyName": "owner", "operation": "unset"},
                    {
                        "propertyName": "publishedAt",
                        "operation": "set",
                        "valueType": "datetime",
                        "value": "2026-01-01T00:00:00Z",
                    },
                ],
            },
        )
        assert response.json() == {
            "resultCode": "0",
            "resultMsg": "success",
            "resultObject": {},
        }

        metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/meeting.md",
            field_names=[
                "status",
                "tags",
                "owner",
                "publishedAt",
                "createdAt",
                "updatedAt",
            ],
        )
        assert metadata["status"] == {"valueType": "string", "value": "active"}
        assert metadata["tags"] == {
            "valueType": "stringList",
            "value": ["existing", "contract", "renewal"],
        }
        assert "owner" not in metadata
        assert metadata["publishedAt"] == {
            "valueType": "datetime",
            "value": "2026-01-01T08:00:00+08:00",
        }
        assert metadata["createdAt"]["value"].endswith("+08:00")
        assert metadata["updatedAt"]["value"].endswith("+08:00")

        failed = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": "/meeting.md",
                "operationList": [
                    {
                        "propertyName": "status",
                        "operation": "set",
                        "valueType": "string",
                        "value": "must-rollback",
                    },
                    {
                        "propertyName": "missingTags",
                        "operation": "append",
                        "value": ["new"],
                    },
                ],
            },
        )
        assert failed.json()["resultCode"] == "-1"

        after_failure = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/meeting.md",
            field_names=["status"],
        )
        assert after_failure["status"] == {
            "valueType": "string",
            "value": "active",
        }

        downloaded_metadata, downloaded_body = split_front_matter(
            _download_file_bytes(client, kb_code=kb_code, file_path="/meeting.md")
        )

    assert downloaded_metadata["status"] == "active"
    assert downloaded_metadata["tags"] == ["existing", "contract", "renewal"]
    assert "owner" not in downloaded_metadata
    assert downloaded_metadata["publishedAt"] == "2026-01-01T08:00:00+08:00"
    assert downloaded_body == b"\n# Meeting\nBody\n"


@pytest.mark.integration
def test_metadata_update_remove_clear_type_change_and_idempotence(monkeypatch):
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)
    markdown = b"""---
tags: [a, b, c]
reviewers: [Alice, Bob]
priority: high
---
# Metadata operations
"""

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata operations {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/operations.md",
            file_content=markdown,
        )
        operations = [
            {
                "propertyName": "tags",
                "operation": "remove",
                "value": ["b", "missing"],
            },
            {"propertyName": "reviewers", "operation": "clear"},
            {
                "propertyName": "priority",
                "operation": "set",
                "valueType": "number",
                "value": 7,
            },
            {"propertyName": "missing", "operation": "unset"},
        ]

        first = _update_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/operations.md",
            operation_list=operations,
        )
        second = _update_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/operations.md",
            operation_list=operations,
        )
        metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/operations.md",
            field_names=["tags", "reviewers", "priority", "missing"],
        )

    assert first.json()["resultCode"] == "0"
    assert second.json()["resultCode"] == "0"
    assert metadata == {
        "tags": {"valueType": "stringList", "value": ["a", "c"]},
        "reviewers": {"valueType": "stringList", "value": []},
        "priority": {"valueType": "number", "value": 7},
    }


@pytest.mark.integration
def test_metadata_update_rejects_invalid_requests_and_missing_resources(monkeypatch):
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata guards {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/guards.md",
            file_content=b"# Guards\n",
        )
        cases = [
            (
                "unknown knowledge base",
                "999999999",
                "/guards.md",
                [{"propertyName": "status", "operation": "unset"}],
                "knowledge base not found",
            ),
            (
                "unknown file",
                kb_code,
                "/missing.md",
                [{"propertyName": "status", "operation": "unset"}],
                "entry not found",
            ),
            (
                "read-only field",
                kb_code,
                "/guards.md",
                [
                    {
                        "propertyName": "fileName",
                        "operation": "set",
                        "valueType": "string",
                        "value": "changed.md",
                    }
                ],
                "metadata field is read-only",
            ),
            (
                "duplicate property",
                kb_code,
                "/guards.md",
                [
                    {"propertyName": "status", "operation": "unset"},
                    {"propertyName": "status", "operation": "unset"},
                ],
                "request validation failed",
            ),
            (
                "invalid operation",
                kb_code,
                "/guards.md",
                [{"propertyName": "status", "operation": "upsert"}],
                "request validation failed",
            ),
            (
                "value type mismatch",
                kb_code,
                "/guards.md",
                [
                    {
                        "propertyName": "priority",
                        "operation": "set",
                        "valueType": "number",
                        "value": True,
                    }
                ],
                "request validation failed",
            ),
            (
                "invalid value type",
                kb_code,
                "/guards.md",
                [
                    {
                        "propertyName": "payload",
                        "operation": "set",
                        "valueType": "json",
                        "value": {},
                    }
                ],
                "request validation failed",
            ),
        ]

        for label, target_kb, file_path, operations, message in cases:
            response = _update_file_metadata(
                client,
                kb_code=target_kb,
                file_path=file_path,
                operation_list=operations,
            )
            payload = response.json()
            assert response.status_code == 200, label
            assert payload["resultCode"] == "-1", label
            assert message in payload["resultMsg"], label
            assert payload["resultObject"] == {}, label

        large_batch = _update_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/guards.md",
            operation_list=[
                {"propertyName": f"field{index}", "operation": "unset"}
                for index in range(101)
            ],
        )
        assert large_batch.json()["resultCode"] == "0"


@pytest.mark.integration
async def test_metadata_is_deactivated_with_file_directory_and_kb_deletion(monkeypatch):
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        file_kb = _create_kb(client, f"Metadata file delete {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=file_kb,
            file_path="/file.md",
            file_content=b"---\nstatus: active\n---\n# File\n",
        )
        assert await _metadata_row_counts(settings, kb_code=file_kb) == (2, 0)
        file_delete = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": file_kb, "filePath": "/file.md"},
        )
        assert file_delete.json()["resultCode"] == "0"
        assert await _metadata_row_counts(settings, kb_code=file_kb) == (0, 2)

        directory_kb = _create_kb(
            client, f"Metadata directory delete {uuid4().hex[:12]}"
        )
        _create_directory(client, kb_code=directory_kb, directory_path="/docs")
        for name in ("a.md", "b.md"):
            _upload_file(
                client,
                kb_code=directory_kb,
                file_path=f"/docs/{name}",
                file_content=f"---\nowner: {name}\n---\n# Doc\n".encode(),
            )
        assert await _metadata_row_counts(settings, kb_code=directory_kb) == (4, 0)
        directory_delete = client.post(
            "/api/v1/directories/delete",
            json={"knCode": directory_kb, "directoryPath": "/docs"},
        )
        assert directory_delete.json()["resultCode"] == "0"
        assert await _metadata_row_counts(settings, kb_code=directory_kb) == (0, 4)

        deleted_kb = _create_kb(client, f"Metadata KB delete {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=deleted_kb,
            file_path="/kb.md",
            file_content=b"---\ncategory: policy\n---\n# KB\n",
        )
        assert await _metadata_row_counts(settings, kb_code=deleted_kb) == (2, 0)
        kb_delete = client.post(
            "/api/v1/knowledgeBases/delete",
            json={"knCode": deleted_kb},
        )
        assert kb_delete.json()["resultCode"] == "0"
        assert await _metadata_row_counts(settings, kb_code=deleted_kb) == (0, 2)


@pytest.mark.integration
def test_metadata_update_serializes_concurrent_appends(monkeypatch):
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata concurrency {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/concurrent.md",
            file_content=b"---\ntags: [base]\n---\n# Concurrent\n",
        )

    def append_tag(tag: str):
        with TestClient(main_module.app) as client:
            return _update_file_metadata(
                client,
                kb_code=kb_code,
                file_path="/concurrent.md",
                operation_list=[
                    {
                        "propertyName": "tags",
                        "operation": "append",
                        "value": [tag],
                    }
                ],
            ).json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append_tag, ["one", "two"]))

    with TestClient(main_module.app) as client:
        metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/concurrent.md",
            field_names=["tags"],
        )

    assert [result["resultCode"] for result in results] == ["0", "0"]
    assert metadata["tags"]["valueType"] == "stringList"
    assert metadata["tags"]["value"][0] == "base"
    assert set(metadata["tags"]["value"]) == {"base", "one", "two"}


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

    class RecordingPublisher:
        def __init__(self):
            self.events = []

        async def publish(self, event):
            self.events.append(event)

    publisher = RecordingPublisher()
    main_module._knowledge_event_publisher_invoker = KnowledgeEventPublisherInvoker(
        publisher
    )

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
    assert not any(
        event.event_type == "build.file.completed" for event in publisher.events
    )


@pytest.mark.integration
def test_failed_build_status_can_be_retried_to_complete(monkeypatch, tmp_path):
    """Failed builds should surface failed status first, then allow a successful retry."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FailingOnceDocumentChunkingService(markdown_text="retry\nworks\n"),
    )

    class RecordingPublisher:
        def __init__(self):
            self.events = []

        async def publish(self, event):
            self.events.append(event)

    publisher = RecordingPublisher()
    main_module._knowledge_event_publisher_invoker = KnowledgeEventPublisherInvoker(
        publisher
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
    build_events = [
        event
        for event in publisher.events
        if event.event_type == "build.file.completed"
    ]
    assert [event.payload.status for event in build_events] == ["failed", "complete"]
    assert build_events[0].payload.error.code == "BUILD_FAILED"
    assert build_events[0].payload.result is None
    assert build_events[1].payload.error is None
    assert build_events[1].payload.result.chunk_count == 1


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
def test_list_dir_and_glob_return_timestamps_latest_build_state_and_empty_metadata(
    monkeypatch, tmp_path
):
    """Browse APIs expose the same fixed entry fields without metadata selection."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Built\n"),
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Browse fields {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/docs/built.md",
            file_content=b"# Built source\n",
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/docs/unbuilt.md",
            file_content=b"# Unbuilt\n",
        )

        root_response = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        list_response = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        glob_response = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/docs/*.md"},
        )

    root_item = root_response.json()["resultObject"]["data"][0]
    assert root_item["type"] == "directory"
    assert root_item["size"] == 0
    assert root_item["updatedAt"]
    assert root_item["buildStatus"] is None
    assert root_item["buildCurrentStep"] is None
    assert root_item["metadata"] == {}

    listed = {
        item["name"]: item for item in list_response.json()["resultObject"]["data"]
    }
    globbed = {
        item["name"]: item for item in glob_response.json()["resultObject"]["data"]
    }
    assert listed == globbed
    assert set(listed) == {"/docs/built.md", "/docs/unbuilt.md"}
    assert listed["/docs/built.md"]["buildStatus"] == "complete"
    assert listed["/docs/built.md"]["buildCurrentStep"] == "complete"
    assert listed["/docs/unbuilt.md"]["buildStatus"] is None
    assert listed["/docs/unbuilt.md"]["buildCurrentStep"] is None
    for item in listed.values():
        assert item["size"] > 0
        assert item["updatedAt"]
        assert item["metadata"] == {}


@pytest.mark.integration
async def test_browse_returns_each_latest_terminal_or_running_build_state(
    monkeypatch, tmp_path
):
    """Browse state uses the latest task per file across every supported status."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    paths = {
        "running.md": [("running", "markdown")],
        "failed.md": [("failed", "chunking")],
        "unsupported.png": [("unsupported", "markdown")],
        "latest.md": [("failed", "markdown"), ("complete", "complete")],
    }

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Browse statuses {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/states")
        for name in paths:
            _upload_file(
                client,
                kb_code=kb_code,
                file_path=f"/states/{name}",
                file_content=b"state",
                content_type=(
                    "image/png" if name.endswith(".png") else "text/markdown"
                ),
            )

        connection = await build_connection_factory(settings)()
        try:
            cursor = connection.cursor()
            for name, task_states in paths.items():
                for status, current_step in task_states:
                    await cursor.execute(
                        """
                        INSERT INTO knowledge_build_task (
                            knowledge_base_id,
                            fs_entry_id,
                            status,
                            current_step,
                            created_at,
                            updated_at
                        )
                        SELECT
                            %(knowledge_base_id)s,
                            kid,
                            %(status)s,
                            %(current_step)s,
                            NOW(),
                            NOW()
                        FROM knowledge_fs_entry
                        WHERE knowledge_base_id = %(knowledge_base_id)s
                          AND virtual_path = %(virtual_path)s
                          AND is_deleted = false
                        """,
                        {
                            "knowledge_base_id": int(kb_code),
                            "virtual_path": f"/states/{name}",
                            "status": status,
                            "current_step": current_step,
                        },
                    )
            await connection.commit()
        finally:
            await connection.close()

        listed_response = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/states"},
        )
        globbed_response = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/states/*"},
        )

    listed = {
        item["name"].rsplit("/", 1)[-1]: (
            item["buildStatus"],
            item["buildCurrentStep"],
        )
        for item in listed_response.json()["resultObject"]["data"]
    }
    globbed = {
        item["name"].rsplit("/", 1)[-1]: (
            item["buildStatus"],
            item["buildCurrentStep"],
        )
        for item in globbed_response.json()["resultObject"]["data"]
    }
    assert (
        listed
        == globbed
        == {
            "running.md": ("running", "markdown"),
            "failed.md": ("failed", "chunking"),
            "unsupported.png": ("unsupported", "markdown"),
            "latest.md": ("complete", "complete"),
        }
    )


@pytest.mark.integration
def test_browse_optional_pagination_preserves_unpaged_shape_and_stable_order(
    monkeypatch, tmp_path
):
    """Pagination is opt-in and slices one directory-first global ordering."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Browse pagination {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/entries")
        _create_directory(client, kb_code=kb_code, directory_path="/entries/Zulu")
        _create_directory(client, kb_code=kb_code, directory_path="/entries/alpha")
        for name in ("z.md", "Beta.md", "a.md"):
            _upload_file(
                client,
                kb_code=kb_code,
                file_path=f"/entries/{name}",
                file_content=name.encode(),
            )

        unpaged_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/entries"},
        ).json()["resultObject"]
        unpaged_glob = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/entries/*"},
        ).json()["resultObject"]
        list_pages = [
            client.post(
                "/api/v1/listDir",
                json={
                    "knCode": kb_code,
                    "directoryPath": "/entries",
                    "pageNum": page_num,
                    "pageSize": 2,
                },
            ).json()["resultObject"]
            for page_num in range(1, 5)
        ]
        glob_first_page = client.post(
            "/api/v1/glob",
            json={
                "knCode": kb_code,
                "pathRule": "/entries/*",
                "pageSize": 2,
            },
        ).json()["resultObject"]
        invalid = client.post(
            "/api/v1/listDir",
            json={
                "knCode": kb_code,
                "directoryPath": "/entries",
                "pageNum": 2,
            },
        ).json()

    expected_names = [
        "/entries/alpha",
        "/entries/Zulu",
        "/entries/a.md",
        "/entries/Beta.md",
        "/entries/z.md",
    ]
    assert set(unpaged_list) == {"data"}
    assert set(unpaged_glob) == {"data"}
    assert [item["name"] for item in unpaged_list["data"]] == expected_names
    assert unpaged_glob["data"] == unpaged_list["data"]

    assert [page["total"] for page in list_pages] == [5, 5, 5, 5]
    assert [page["pageNum"] for page in list_pages] == [1, 2, 3, 4]
    assert [page["pageSize"] for page in list_pages] == [2, 2, 2, 2]
    combined_names = [item["name"] for page in list_pages[:3] for item in page["data"]]
    assert combined_names == expected_names
    assert list_pages[3]["data"] == []
    assert glob_first_page == list_pages[0]
    assert invalid["resultCode"] == "-1"
    assert invalid["resultMsg"] == "request validation failed"


@pytest.mark.integration
def test_browse_validates_page_edges_and_globally_pages_selected_metadata(
    monkeypatch, tmp_path
):
    """Browse edge cases preserve global ordering and page-local metadata."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Browse edges {uuid4().hex[:12]}")
        for directory_path in ("/group-z", "/group-a"):
            _create_directory(
                client,
                kb_code=kb_code,
                directory_path=directory_path,
            )
        for file_path, marker in (
            ("/group-z/alpha.md", "z-lower"),
            ("/group-z/Alpha.md", "z-upper"),
            ("/group-a/zulu.md", "a-zulu"),
        ):
            response = client.post(
                "/api/v1/knowledgeItems/import",
                data={
                    "knCode": kb_code,
                    "filePath": file_path,
                    "metadata": json.dumps({"marker": marker}),
                },
                files={
                    "fileContent": (
                        file_path.rsplit("/", 1)[-1],
                        marker.encode(),
                        "text/markdown",
                    )
                },
            )
            assert response.json()["resultCode"] == "0", response.json()

        unpaged = client.post(
            "/api/v1/glob",
            json={
                "knCode": kb_code,
                "pathRule": "/group-*/*",
                "metadataFieldList": [],
            },
        ).json()["resultObject"]
        empty_list_selection = client.post(
            "/api/v1/listDir",
            json={
                "knCode": kb_code,
                "directoryPath": "/group-z",
                "metadataFieldList": [],
            },
        ).json()["resultObject"]["data"]
        pages = [
            client.post(
                "/api/v1/glob",
                json={
                    "knCode": kb_code,
                    "pathRule": "/group-*/*",
                    "metadataFieldList": ["marker"],
                    "pageNum": page_num,
                    "pageSize": 1,
                },
            ).json()["resultObject"]
            for page_num in range(1, 4)
        ]
        invalid_payloads = (
            {"pageNum": 0, "pageSize": 1},
            {"pageNum": -1, "pageSize": 1},
            {"pageSize": 0},
            {"pageSize": -1},
            {"pageSize": 10001},
        )
        invalid_results = []
        for endpoint, base in (
            ("/api/v1/listDir", {"directoryPath": "/group-z"}),
            ("/api/v1/glob", {"pathRule": "/group-*/*"}),
        ):
            for invalid_payload in invalid_payloads:
                invalid_results.append(
                    client.post(
                        endpoint,
                        json={"knCode": kb_code, **base, **invalid_payload},
                    ).json()
                )

    expected = [
        ("/group-a/zulu.md", "a-zulu"),
        ("/group-z/Alpha.md", "z-upper"),
        ("/group-z/alpha.md", "z-lower"),
    ]
    assert set(unpaged) == {"data"}
    assert [(item["name"], item["metadata"]) for item in unpaged["data"]] == [
        (name, {}) for name, _ in expected
    ]
    assert all(item["metadata"] == {} for item in empty_list_selection)
    assert [page["total"] for page in pages] == [3, 3, 3]
    assert [
        (
            page["data"][0]["name"],
            page["data"][0]["metadata"]["marker"]["value"],
        )
        for page in pages
    ] == expected
    assert all(result["resultCode"] == "-1" for result in invalid_results)
    assert all(
        result["resultMsg"] == "request validation failed" for result in invalid_results
    )


@pytest.mark.integration
def test_browse_metadata_field_list_returns_selected_nested_metadata(
    monkeypatch, tmp_path
):
    """Browse metadata matches search-style selection and stays nested per item."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Browse metadata {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/metadata")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/metadata/a.md",
            file_content=b"# A\n",
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/metadata/b.md",
            file_content=b"# B\n",
        )
        updated = _update_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/metadata/a.md",
            operation_list=[
                {
                    "propertyName": "owner",
                    "operation": "set",
                    "valueType": "string",
                    "value": "Alice",
                },
                {
                    "propertyName": "amount",
                    "operation": "set",
                    "valueType": "number",
                    "value": 3.5,
                },
                {
                    "propertyName": "active",
                    "operation": "set",
                    "valueType": "boolean",
                    "value": True,
                },
                {
                    "propertyName": "publishedAt",
                    "operation": "set",
                    "valueType": "datetime",
                    "value": "2026-08-30T01:02:03Z",
                },
                {
                    "propertyName": "tags",
                    "operation": "set",
                    "valueType": "stringList",
                    "value": ["one", "two"],
                },
            ],
        )
        assert updated.json()["resultCode"] == "0"

        fields = [
            "owner",
            "amount",
            "active",
            "publishedAt",
            "tags",
            "updatedAt",
            "fileSize",
            "filePath",
        ]
        listed = client.post(
            "/api/v1/listDir",
            json={
                "knCode": kb_code,
                "directoryPath": "/metadata",
                "metadataFieldList": fields,
            },
        ).json()["resultObject"]["data"]
        globbed = client.post(
            "/api/v1/glob",
            json={
                "knCode": kb_code,
                "pathRule": "/metadata/*",
                "metadataFieldList": fields,
            },
        ).json()["resultObject"]["data"]
        unknown = client.post(
            "/api/v1/listDir",
            json={
                "knCode": kb_code,
                "directoryPath": "/metadata",
                "metadataFieldList": ["doesNotExist"],
                "pageSize": 1,
            },
        ).json()["resultObject"]

    assert listed == globbed
    by_name = {item["name"]: item for item in listed}
    metadata = by_name["/metadata/a.md"]["metadata"]
    assert metadata["owner"] == {"valueType": "string", "value": "Alice"}
    assert metadata["amount"] == {"valueType": "number", "value": 3.5}
    assert metadata["active"] == {"valueType": "boolean", "value": True}
    assert metadata["publishedAt"]["valueType"] == "datetime"
    assert metadata["publishedAt"]["value"].endswith("+08:00")
    assert metadata["tags"] == {
        "valueType": "stringList",
        "value": ["one", "two"],
    }
    assert metadata["updatedAt"]["value"] == by_name["/metadata/a.md"]["updatedAt"]
    assert metadata["fileSize"]["value"] == by_name["/metadata/a.md"]["size"]
    assert metadata["filePath"]["value"] == "/metadata/a.md"
    assert by_name["/metadata/b.md"]["metadata"] == {
        "updatedAt": {
            "valueType": "datetime",
            "value": by_name["/metadata/b.md"]["updatedAt"],
        },
        "fileSize": {
            "valueType": "number",
            "value": by_name["/metadata/b.md"]["size"],
        },
        "filePath": {"valueType": "string", "value": "/metadata/b.md"},
    }
    assert unknown["data"][0]["metadata"] == {}


@pytest.mark.integration
def test_qa_browse_tool_contract_forwards_selected_metadata_to_live_api(
    monkeypatch, tmp_path
):
    """QA tool schemas serialize metadataFieldList accepted by both live browse APIs."""
    from by_qa.qa.common.operation_registry import OPERATION_REGISTRY, OperationType

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"QA browse metadata {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/tool")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/tool/child",
            metadata={"owner": "Alice", "hidden": "must-not-return"},
        )

        cases = [
            (
                OperationType.LIST_DIR,
                "/api/v1/listDir",
                {
                    "knCode": kb_code,
                    "directoryPath": "/tool",
                    "metadataFieldList": ["owner"],
                    "pageSize": 10,
                },
            ),
            (
                OperationType.GLOB,
                "/api/v1/glob",
                {
                    "knCode": kb_code,
                    "pathRule": "/tool/*",
                    "metadataFieldList": ["owner"],
                    "pageSize": 10,
                },
            ),
        ]
        results = []
        for operation_type, endpoint, tool_arguments in cases:
            request_body = (
                OPERATION_REGISTRY[operation_type]
                .input_schema.model_validate(tool_arguments)
                .model_dump(
                    by_alias=True,
                    exclude_none=True,
                )
            )
            response = client.post(endpoint, json=request_body).json()
            assert response["resultCode"] == "0", response
            results.append(response["resultObject"])

    assert results[0] == results[1]
    assert results[0]["pageNum"] == 1
    assert results[0]["pageSize"] == 10
    assert results[0]["total"] == 1
    assert results[0]["data"][0]["metadata"] == {
        "owner": {"valueType": "string", "value": "Alice"}
    }


@pytest.mark.integration
def test_directory_create_and_rename_share_entry_metadata_lifecycle(
    monkeypatch, tmp_path
):
    """New parent directories inherit request metadata, which survives renames."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Directory metadata {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/parent/child",
            metadata={
                "owner": "hr",
                "priority": 2,
                "active": True,
                "tags": ["policy", "draft"],
            },
        )

        child = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/parent/child",
            field_names=["owner", "priority", "active", "tags"],
        )
        parent = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/parent",
            field_names=["owner", "priority", "active", "tags"],
        )
        assert child == {
            "owner": {"valueType": "string", "value": "hr"},
            "priority": {"valueType": "number", "value": 2.0},
            "active": {"valueType": "boolean", "value": True},
            "tags": {"valueType": "stringList", "value": ["policy", "draft"]},
        }
        assert parent == {
            "owner": {"valueType": "string", "value": "hr"},
            "priority": {"valueType": "number", "value": 2.0},
            "active": {"valueType": "boolean", "value": True},
            "tags": {"valueType": "stringList", "value": ["policy", "draft"]},
        }
        all_child = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": "/parent/child"},
        ).json()["resultObject"]["metadata"]
        assert all_child["owner"]["value"] == "hr"
        assert all_child["fileName"]["value"] == "child"
        assert all_child["filePath"]["value"] == "/parent/child"
        assert all_child["fileSize"]["value"] == 0
        assert all_child["fileType"]["value"] == ""
        assert all_child["mimeType"]["value"] is None
        assert all_child["fileSignature"]["value"] is None
        assert all_child["createdAt"]["value"]
        assert all_child["updatedAt"]["value"]
        listed = client.post(
            "/api/v1/listDir",
            json={
                "knCode": kb_code,
                "directoryPath": "/parent",
                "metadataFieldList": ["owner"],
            },
        ).json()["resultObject"]["data"]
        globbed = client.post(
            "/api/v1/glob",
            json={
                "knCode": kb_code,
                "pathRule": "/parent/*",
                "metadataFieldList": ["owner"],
            },
        ).json()["resultObject"]["data"]
        assert listed == globbed
        assert listed[0]["type"] == "directory"
        assert listed[0]["metadata"] == {
            "owner": {"valueType": "string", "value": "hr"}
        }

        # Existing-directory create remains idempotent while applying the upsert.
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/parent/child",
            metadata={"owner": "operations", "createdAgain": True},
        )
        rename = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/parent/child",
                "directoryName": "renamed",
                "metadata": {"owner": "legal", "retention": 7},
            },
        ).json()
        old_path = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": "/parent/child",
                "metadataFieldList": ["owner"],
            },
        ).json()
        renamed = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/parent/renamed",
            field_names=["owner", "retention", "tags", "createdAgain"],
        )

    assert rename["resultCode"] == "0"
    assert old_path["resultCode"] == "-1"
    assert old_path["resultMsg"] == "entry not found: /parent/child"
    assert renamed == {
        "owner": {"valueType": "string", "value": "legal"},
        "retention": {"valueType": "number", "value": 7.0},
        "tags": {"valueType": "stringList", "value": ["policy", "draft"]},
        "createdAgain": {"valueType": "boolean", "value": True},
    }


@pytest.mark.integration
def test_empty_directory_metadata_disappears_from_every_entry_interface(
    monkeypatch, tmp_path
):
    """Deleting a metadata-bearing empty directory hides its entry and values."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Empty directory metadata {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/empty",
            metadata={"emptyOnly": "value"},
        )
        deleted = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/empty"},
        ).json()
        metadata_get = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": "/empty",
                "metadataFieldList": ["emptyOnly"],
            },
        ).json()
        metadata_update = _update_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/empty",
            operation_list=[
                {
                    "propertyName": "emptyOnly",
                    "operation": "set",
                    "valueType": "string",
                    "value": "new",
                }
            ],
        ).json()
        listed = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        ).json()["resultObject"]["data"]
        globbed = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/*"},
        ).json()["resultObject"]["data"]

    assert deleted["resultCode"] == "0"
    assert metadata_get["resultCode"] == "-1"
    assert metadata_update["resultCode"] == "-1"
    assert listed == []
    assert globbed == []


@pytest.mark.integration
def test_directory_metadata_update_operations_are_atomic(monkeypatch, tmp_path):
    """Directory paths support every metadata operation and whole-batch rollback."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Directory operations {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/docs",
            metadata={
                "status": "draft",
                "owner": "Alice",
                "tags": ["existing", "contract"],
                "removeTags": ["existing", "keep"],
                "toClear": ["one"],
            },
        )
        updated = _update_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs",
            operation_list=[
                {
                    "propertyName": "status",
                    "operation": "set",
                    "valueType": "string",
                    "value": "active",
                },
                {
                    "propertyName": "tags",
                    "operation": "append",
                    "value": ["contract", "renewal"],
                },
                {
                    "propertyName": "removeTags",
                    "operation": "remove",
                    "value": ["existing"],
                },
                {"propertyName": "toClear", "operation": "clear"},
                {"propertyName": "owner", "operation": "unset"},
            ],
        ).json()
        metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs",
            field_names=["status", "owner", "tags", "removeTags", "toClear"],
        )
        failed = _update_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs",
            operation_list=[
                {
                    "propertyName": "status",
                    "operation": "set",
                    "valueType": "string",
                    "value": "must-rollback",
                },
                {
                    "propertyName": "missingTags",
                    "operation": "append",
                    "value": ["new"],
                },
            ],
        ).json()
        after_failure = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs",
            field_names=["status"],
        )

    assert updated["resultCode"] == "0"
    assert metadata == {
        "status": {"valueType": "string", "value": "active"},
        "tags": {
            "valueType": "stringList",
            "value": ["existing", "contract", "renewal"],
        },
        "removeTags": {"valueType": "stringList", "value": ["keep"]},
        "toClear": {"valueType": "stringList", "value": []},
    }
    assert failed["resultCode"] == "-1"
    assert after_failure == {"status": {"valueType": "string", "value": "active"}}


@pytest.mark.integration
async def test_directory_metadata_follows_move_and_is_removed_with_subtree(
    monkeypatch, tmp_path
):
    """Directory and descendant metadata follow stable entries, then soft-delete together."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Directory move metadata {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/archive")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/tree",
            metadata={"scope": "root"},
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/tree/child",
            metadata={"scope": "child"},
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/tree/child/a.md",
            file_content=b"# A\n",
        )
        assert (
            _update_file_metadata(
                client,
                kb_code=kb_code,
                file_path="/tree/child/a.md",
                operation_list=[
                    {
                        "propertyName": "scope",
                        "operation": "set",
                        "valueType": "string",
                        "value": "file",
                    }
                ],
            ).json()["resultCode"]
            == "0"
        )

        moved = _move_items(
            client,
            kb_code=kb_code,
            source_path=["/tree"],
            target_directory_path="/archive",
        )
        root_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/archive/tree",
            field_names=["scope"],
        )
        child_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/archive/tree/child",
            field_names=["scope"],
        )
        file_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/archive/tree/child/a.md",
            field_names=["scope"],
        )
        before_counts = await _metadata_row_counts(settings, kb_code=kb_code)
        deleted = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/archive/tree"},
        ).json()
        after_counts = await _metadata_row_counts(settings, kb_code=kb_code)
        deleted_gets = [
            client.post(
                "/api/v1/knowledgeItems/metadata/get",
                json={
                    "knCode": kb_code,
                    "filePath": path,
                    "metadataFieldList": ["scope"],
                },
            ).json()
            for path in (
                "/archive/tree",
                "/archive/tree/child",
                "/archive/tree/child/a.md",
            )
        ]
        listed = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/archive"},
        ).json()["resultObject"]["data"]
        globbed = client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": "/archive/*"},
        ).json()["resultObject"]["data"]

    assert moved[0]["targetPath"] == "/archive/tree"
    assert root_metadata["scope"]["value"] == "root"
    assert child_metadata["scope"]["value"] == "child"
    assert file_metadata["scope"]["value"] == "file"
    assert before_counts[0] >= 3
    assert deleted["resultCode"] == "0"
    assert after_counts[0] == 0
    assert after_counts[1] >= before_counts[0]
    assert all(item["resultCode"] == "-1" for item in deleted_gets)
    assert listed == []
    assert globbed == []


@pytest.mark.integration
def test_move_metadata_applies_only_to_new_target_directories(monkeypatch, tmp_path):
    """Move metadata initializes new targets without mutating sources or ancestors."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Move target metadata {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/existing",
            metadata={"scope": "existing"},
        )
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/source-dir",
            metadata={"scope": "source-directory"},
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/source-dir/child.md",
            file_content=b"# Child\n",
            metadata={"scope": "source-child"},
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/source-file.md",
            file_content=b"# File\n",
            metadata={"scope": "source-file"},
        )

        moved_directory = _move_items(
            client,
            kb_code=kb_code,
            source_path=["/source-dir"],
            target_directory_path="/new/branch",
            metadata={"scope": "directory-target"},
        )
        moved_file = _move_items(
            client,
            kb_code=kb_code,
            source_path=["/source-file.md"],
            target_file_path="/existing/new-parent/renamed.md",
            metadata={"scope": "file-target"},
        )
        metadata_by_path = {
            path: _get_file_metadata(
                client,
                kb_code=kb_code,
                file_path=path,
                field_names=["scope"],
            )
            for path in (
                "/new",
                "/new/branch",
                "/new/branch/source-dir",
                "/new/branch/source-dir/child.md",
                "/existing",
                "/existing/new-parent",
                "/existing/new-parent/renamed.md",
            )
        }

    assert moved_directory[0]["targetPath"] == "/new/branch/source-dir"
    assert moved_file[0]["targetPath"] == "/existing/new-parent/renamed.md"
    assert metadata_by_path == {
        "/new": {"scope": {"valueType": "string", "value": "directory-target"}},
        "/new/branch": {"scope": {"valueType": "string", "value": "directory-target"}},
        "/new/branch/source-dir": {
            "scope": {"valueType": "string", "value": "source-directory"}
        },
        "/new/branch/source-dir/child.md": {
            "scope": {"valueType": "string", "value": "source-child"}
        },
        "/existing": {"scope": {"valueType": "string", "value": "existing"}},
        "/existing/new-parent": {
            "scope": {"valueType": "string", "value": "file-target"}
        },
        "/existing/new-parent/renamed.md": {
            "scope": {"valueType": "string", "value": "source-file"}
        },
    }


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
    downloaded_metadata, downloaded_body = split_front_matter(response.content)
    assert downloaded_metadata == {"documentKind": "original"}
    assert downloaded_body == original_content.encode("utf-8")
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

    assert after.status_code == 200
    after_items = after.json()["resultObject"]["data"]
    assert len(after_items) >= 1
    assert "2025" in after_items[0]["filePath"]


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
async def test_markdown_references_resolve_break_and_restore_through_read_and_search(
    monkeypatch, tmp_path
):
    """Stable Markdown references should resolve at read/search time as targets change."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())
    await _set_search_service(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/resolved/b.md",
            file_content=b"# b\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/resolved/a.md",
            file_content=b"see [b](b.md)\nresolved unique alpha\n",
        )

        resolved_read = _read_file_data(
            client, kb_code=kb_code, file_path="/resolved/a.md"
        )
        resolved_search = _search_chunk_texts(
            client, kb_code=kb_code, query="resolved unique alpha"
        )

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/pending/a.md",
            file_content=b"see [b](b.md)\npending unique beta\n",
        )
        pending_before_target = _read_file_data(
            client, kb_code=kb_code, file_path="/pending/a.md"
        )

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/pending/b.md",
            file_content=b"# later b\n",
        )
        pending_after_target = _read_file_data(
            client, kb_code=kb_code, file_path="/pending/a.md"
        )

        delete_target = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": "/pending/b.md"},
        )
        pending_after_delete = _read_file_data(
            client, kb_code=kb_code, file_path="/pending/a.md"
        )
        search_after_delete = _search_chunk_texts(
            client, kb_code=kb_code, query="pending unique beta"
        )

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/pending/b.md",
            file_content=b"# restored b\n",
        )
        pending_after_restore = _read_file_data(
            client, kb_code=kb_code, file_path="/pending/a.md"
        )
        search_after_restore = _search_chunk_texts(
            client, kb_code=kb_code, query="pending unique beta"
        )

    assert "(/resolved/b.md)" in resolved_read
    assert "byqa-ref://" not in resolved_read
    assert any("(/resolved/b.md)" in text for text in resolved_search)
    assert all("byqa-ref://" not in text for text in resolved_search)

    assert "(/pending/b.md)" in pending_before_target
    assert "byqa-ref://" not in pending_before_target
    assert "(/pending/b.md)" in pending_after_target
    assert delete_target.status_code == 200
    assert "(/pending/b.md)" in pending_after_delete
    assert "byqa-ref://" not in pending_after_delete
    assert any("(/pending/b.md)" in text for text in search_after_delete)
    assert all("byqa-ref://" not in text for text in search_after_delete)
    assert "(/pending/b.md)" in pending_after_restore
    assert any("(/pending/b.md)" in text for text in search_after_restore)
    assert all("byqa-ref://" not in text for text in search_after_restore)


@pytest.mark.integration
async def test_markdown_references_follow_file_and_subtree_moves_without_rebuild(
    monkeypatch, tmp_path
):
    """Moving targets should update read/search reference output without rebuilding chunks."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    fake_chunking = EchoDocumentChunkingService()
    _set_document_chunking_service(monkeypatch, fake_chunking)
    await _set_search_service(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/move/targets/b.md",
            file_content=b"# target b\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/move/source.md",
            file_content=b"see [b](/move/targets/b.md)\nmove unique alpha\n",
        )
        chunk_calls_before_target_move = len(fake_chunking.chunk_calls)
        moved_file = _move_items(
            client,
            kb_code=kb_code,
            source_path=["/move/targets/b.md"],
            target_file_path="/moved/auto/renamed-b.md",
        )
        chunk_calls_after_target_move = len(fake_chunking.chunk_calls)
        moved_file_read = _read_file_data(
            client, kb_code=kb_code, file_path="/move/source.md"
        )
        moved_file_search = _search_chunk_texts(
            client, kb_code=kb_code, query="move unique alpha"
        )
        kb_root_after_target_move = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        moved_root_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/moved"},
        )
        moved_auto_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/moved/auto"},
        )

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/tree/sub/one.md",
            file_content=b"# one\n",
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/tree/sub/two.md",
            file_content=b"# two\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/tree/sub/indexed.md",
            file_content=b"tree indexed unique\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/refs/one.md",
            file_content=b"one [target](/tree/sub/one.md)\nsubtree unique one\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/refs/two.md",
            file_content=b"two [target](/tree/sub/two.md)\nsubtree unique two\n",
        )
        chunk_calls_before_subtree_move = list(fake_chunking.chunk_calls)
        moved_subtree = _move_items(
            client,
            kb_code=kb_code,
            source_path=["/tree"],
            target_directory_path="/archive/auto",
        )
        chunk_calls_after_subtree_move = list(fake_chunking.chunk_calls)
        subtree_one_read = _read_file_data(
            client, kb_code=kb_code, file_path="/refs/one.md"
        )
        subtree_two_read = _read_file_data(
            client, kb_code=kb_code, file_path="/refs/two.md"
        )
        subtree_one_search = _search_chunk_texts(
            client, kb_code=kb_code, query="subtree unique one"
        )
        subtree_two_search = _search_chunk_texts(
            client, kb_code=kb_code, query="subtree unique two"
        )
        tree_index_search_items = _search_items(
            client, kb_code=kb_code, query="tree indexed unique"
        )
        kb_root_after_subtree_move = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        archive_root_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/archive"},
        )
        archive_auto_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/archive/auto"},
        )

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/pending-source/source.md",
            file_content=b"see [missing](missing.md)\npending source move\n",
        )
        moved_source = _move_items(
            client,
            kb_code=kb_code,
            source_path=["/pending-source/source.md"],
            target_file_path="/new/source/path/source.md",
        )
        moved_source_read = _read_file_data(
            client, kb_code=kb_code, file_path="/new/source/path/source.md"
        )
        moved_source_search_items = _search_items(
            client, kb_code=kb_code, query="pending source move"
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/pending-source/missing.md",
            file_content=b"# restored missing\n",
        )
        moved_source_after_old_target_upload = _read_file_data(
            client, kb_code=kb_code, file_path="/new/source/path/source.md"
        )
        new_root_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        new_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/new"},
        )
        new_source_root_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/new/source"},
        )
        new_source_path_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/new/source/path"},
        )

    assert moved_file[0]["targetPath"] == "/moved/auto/renamed-b.md"
    assert chunk_calls_before_target_move == 1
    assert chunk_calls_after_target_move == chunk_calls_before_target_move
    assert kb_root_after_target_move.status_code == 200
    assert moved_root_list.status_code == 200
    assert moved_auto_list.status_code == 200
    assert "/moved" in _list_dir_entry_names(kb_root_after_target_move)
    assert "/moved/auto" in _list_dir_entry_names(moved_root_list)
    assert "/moved/auto/renamed-b.md" in _list_dir_entry_names(moved_auto_list)
    assert "(/moved/auto/renamed-b.md)" in moved_file_read
    assert any("(/moved/auto/renamed-b.md)" in text for text in moved_file_search)
    assert all("byqa-ref://" not in text for text in moved_file_search)

    assert moved_subtree[0]["targetPath"] == "/archive/auto/tree"
    assert len(chunk_calls_before_subtree_move) == 4
    assert chunk_calls_after_subtree_move == chunk_calls_before_subtree_move
    assert kb_root_after_subtree_move.status_code == 200
    assert archive_root_list.status_code == 200
    assert archive_auto_list.status_code == 200
    assert "/archive" in _list_dir_entry_names(kb_root_after_subtree_move)
    assert "/archive/auto" in _list_dir_entry_names(archive_root_list)
    assert "/archive/auto/tree" in _list_dir_entry_names(archive_auto_list)
    assert "(/archive/auto/tree/sub/one.md)" in subtree_one_read
    assert "(/archive/auto/tree/sub/two.md)" in subtree_two_read
    assert any(
        item["filePath"].endswith("archive/auto/tree/sub/indexed.md")
        for item in tree_index_search_items
    )
    assert "byqa-ref://" not in subtree_one_read
    assert "byqa-ref://" not in subtree_two_read
    assert any("(/archive/auto/tree/sub/one.md)" in text for text in subtree_one_search)
    assert any("(/archive/auto/tree/sub/two.md)" in text for text in subtree_two_search)
    assert all("byqa-ref://" not in text for text in subtree_one_search)
    assert all("byqa-ref://" not in text for text in subtree_two_search)

    assert moved_source[0]["targetPath"] == "/new/source/path/source.md"
    assert new_root_list.status_code == 200
    assert new_list.status_code == 200
    assert new_source_root_list.status_code == 200
    assert new_source_path_list.status_code == 200
    assert "/new" in _list_dir_entry_names(new_root_list)
    assert "/new/source" in _list_dir_entry_names(new_list)
    assert "/new/source/path" in _list_dir_entry_names(new_source_root_list)
    assert "/new/source/path/source.md" in _list_dir_entry_names(new_source_path_list)
    assert any(
        item["filePath"].endswith("new/source/path/source.md")
        for item in moved_source_search_items
    )
    assert "(/pending-source/missing.md)" in moved_source_read
    assert "/new/source/path/missing.md" not in moved_source_read
    assert "(/pending-source/missing.md)" in moved_source_after_old_target_upload
    assert "/new/source/path/missing.md" not in moved_source_after_old_target_upload
    assert "byqa-ref://" not in moved_source_read
    assert "byqa-ref://" not in moved_source_after_old_target_upload


@pytest.mark.integration
async def test_markdown_reference_downloads_resolve_across_move_delete_and_restore(
    monkeypatch, tmp_path
):
    """downloadFile should resolve stable Markdown reference tokens like readFile."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())
    await _set_search_service(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/download/target/b.md",
            file_content=b"# b\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/download/source/a.md",
            file_content=b"download [b](../target/b.md)\ndownload unique alpha\n",
        )
        resolved_download = _download_file_bytes(
            client, kb_code=kb_code, file_path="/download/source/a.md"
        ).decode("utf-8")

        _move_items(
            client,
            kb_code=kb_code,
            source_path=["/download/target/b.md"],
            target_file_path="/download/moved/b.md",
        )
        moved_download = _download_file_bytes(
            client, kb_code=kb_code, file_path="/download/source/a.md"
        ).decode("utf-8")

        delete_target = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": "/download/moved/b.md"},
        )
        broken_download = _download_file_bytes(
            client, kb_code=kb_code, file_path="/download/source/a.md"
        ).decode("utf-8")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/download/moved/b.md",
            file_content=b"# restored\n",
        )
        restored_download = _download_file_bytes(
            client, kb_code=kb_code, file_path="/download/source/a.md"
        ).decode("utf-8")

    assert delete_target.status_code == 200
    assert "(/download/target/b.md)" in resolved_download
    assert "(/download/moved/b.md)" in moved_download
    assert "(/download/target/b.md)" in broken_download
    assert "(/download/moved/b.md)" in restored_download
    assert "byqa-ref://" not in resolved_download
    assert "byqa-ref://" not in moved_download
    assert "byqa-ref://" not in broken_download
    assert "byqa-ref://" not in restored_download


@pytest.mark.integration
async def test_markdown_reference_relations_deduplicate_while_suffixes_stay_per_occurrence(
    monkeypatch, tmp_path
):
    """One relation should serve repeated targets with occurrence-local suffixes."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())
    await _set_search_service(monkeypatch, settings)

    original_target = "/suffix/b.md"
    resolved_target = "/suffix/b.md?download=1#intro"
    resolved_fragment_target = "/suffix/b.md#api"
    moved_target = "/suffix/moved/b.md?download=1#intro"
    moved_fragment_target = "/suffix/moved/b.md#api"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/suffix/b.md",
            file_content=b"# intro\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/suffix/a.md",
            file_content=(
                b"suffix [b](b.md?download=1#intro)\n"
                b"fragment [b](./b.md#api)\n"
                b"plain [b](b.md)\n"
                b"suffix unique alpha\n"
            ),
        )
        resolved_read = _read_file_data(
            client, kb_code=kb_code, file_path="/suffix/a.md"
        )
        resolved_download = _download_file_bytes(
            client, kb_code=kb_code, file_path="/suffix/a.md"
        ).decode("utf-8")
        resolved_search = _search_chunk_texts(
            client, kb_code=kb_code, query="suffix unique alpha"
        )
        resolved_refs = _reference_rows(
            client, kb_code=kb_code, target_path="/suffix/b.md"
        )

        _move_items(
            client,
            kb_code=kb_code,
            source_path=["/suffix/b.md"],
            target_file_path="/suffix/moved/b.md",
        )
        moved_read = _read_file_data(client, kb_code=kb_code, file_path="/suffix/a.md")

        delete_target = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": "/suffix/moved/b.md"},
        )
        broken_read = _read_file_data(client, kb_code=kb_code, file_path="/suffix/a.md")
        broken_download = _download_file_bytes(
            client, kb_code=kb_code, file_path="/suffix/a.md"
        ).decode("utf-8")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/suffix/moved/b.md",
            file_content=b"# restored intro\n",
        )
        restored_read = _read_file_data(
            client, kb_code=kb_code, file_path="/suffix/a.md"
        )

    assert delete_target.status_code == 200
    assert f"({resolved_target})" in resolved_read
    assert f"({resolved_fragment_target})" in resolved_read
    assert "(/suffix/b.md)" in resolved_read
    assert f"({resolved_target})" in resolved_download
    assert f"({resolved_fragment_target})" in resolved_download
    assert any(f"({resolved_target})" in text for text in resolved_search)
    assert any(f"({resolved_fragment_target})" in text for text in resolved_search)
    assert len(resolved_refs) == 1
    assert resolved_refs[0]["originalTarget"] == original_target
    assert resolved_refs[0]["targetSuffix"] == ""
    assert resolved_refs[0]["targetPath"] == "/suffix/b.md"
    assert f"({moved_target})" in moved_read
    assert f"({moved_fragment_target})" in moved_read
    assert f"({original_target})" in broken_read
    assert f"({original_target}?download=1#intro)" in broken_read
    assert f"({original_target}#api)" in broken_read
    assert f"({original_target}?download=1#intro)" in broken_download
    assert "?download=1#intro?download=1#intro" not in broken_read
    assert f"({moved_target})" in restored_read
    assert f"({moved_fragment_target})" in restored_read
    assert "byqa-ref://" not in resolved_read
    assert "byqa-ref://" not in broken_read
    assert "byqa-ref://" not in restored_read


@pytest.mark.integration
def test_read_file_line_window_slices_before_resolving_markdown_references(
    monkeypatch, tmp_path
):
    """Line-window readFile should resolve tokens only after slicing the sidecar."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/window/b.md",
            file_content=b"# b\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/window/a.md",
            file_content=b"line 1\nsee [b](b.md)\nline 3\n",
        )
        line_two = _read_file_window_data(
            client,
            kb_code=kb_code,
            file_path="/window/a.md",
            start_line=2,
            end_line=2,
        )

    assert line_two.strip() == "see [b](/window/b.md)"
    assert "line 1" not in line_two
    assert "line 3" not in line_two
    assert "byqa-ref://" not in line_two


@pytest.mark.integration
def test_reference_query_filters_deleted_source_and_supports_outbound_and_all(
    monkeypatch, tmp_path
):
    """references should hide deleted source rows and expose outbound/all directions."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/refs-api/target.md",
            file_content=b"# target\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/refs-api/source.md",
            file_content=(
                b"known [target](target.md#part)\n"
                b"missing [ghost](ghost.md)\n"
                b"refs api unique\n"
            ),
        )
        all_before_delete = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/refs-api/source.md",
            direction="all",
        )
        outbound = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/refs-api/source.md",
            direction="outbound",
        )
        inbound_before_delete = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/refs-api/target.md",
            direction="inbound",
        )

        delete_source = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": "/refs-api/source.md"},
        )
        inbound_after_delete = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/refs-api/target.md",
            direction="inbound",
        )
        outbound_after_delete = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/refs-api/source.md",
            direction="outbound",
        )

    assert delete_source.status_code == 200
    assert all_before_delete["inbound"] == []
    assert [item["status"] for item in all_before_delete["outbound"]] == [
        "resolved",
        "unresolved",
    ]
    assert outbound["inbound"] == []
    assert outbound["outbound"][0]["sourcePath"] == "/refs-api/source.md"
    assert outbound["outbound"][0]["targetPath"] == "/refs-api/target.md"
    assert outbound["outbound"][0]["targetSuffix"] == ""
    assert outbound["outbound"][1]["targetPath"] == "/refs-api/ghost.md"
    assert inbound_before_delete["inbound"][0]["sourcePath"] == "/refs-api/source.md"
    assert inbound_after_delete["inbound"] == []
    assert outbound_after_delete["outbound"] == []


@pytest.mark.integration
def test_directory_markdown_links_are_not_recorded_as_stable_file_references(
    monkeypatch, tmp_path
):
    """Directory targets are intentionally left as original Markdown links."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        _create_directory(client, kb_code=kb_code, directory_path="/dir-link/assets")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/dir-link/source.md",
            file_content=b"see [assets](assets)\ndir link unique\n",
        )
        read_before_move = _read_file_data(
            client, kb_code=kb_code, file_path="/dir-link/source.md"
        )
        refs_before_move = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/dir-link/source.md",
            direction="outbound",
        )

        _move_items(
            client,
            kb_code=kb_code,
            source_path=["/dir-link/assets"],
            target_directory_path="/dir-link/archive",
        )
        read_after_move = _read_file_data(
            client, kb_code=kb_code, file_path="/dir-link/source.md"
        )
        refs_after_move = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/dir-link/source.md",
            direction="outbound",
        )

    assert "(assets)" in read_before_move
    assert "(assets)" in read_after_move
    assert "/dir-link/archive/assets" not in read_after_move
    assert refs_before_move["outbound"] == []
    assert refs_after_move["outbound"] == []
    assert "byqa-ref://" not in read_after_move


@pytest.mark.integration
async def test_batch_move_updates_multiple_reference_targets_and_invalid_move_is_atomic(
    monkeypatch, tmp_path
):
    """Batch target moves should update all resolved refs; invalid moves should change none."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())
    await _set_search_service(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/batch-targets/a.md",
            file_content=b"# a\n",
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/batch-targets/b.md",
            file_content=b"# b\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/batch-source/source.md",
            file_content=(
                b"a [a](/batch-targets/a.md)\n"
                b"b [b](/batch-targets/b.md)\n"
                b"batch move unique\n"
            ),
        )

        moved = _move_items(
            client,
            kb_code=kb_code,
            source_path=["/batch-targets/a.md", "/batch-targets/b.md"],
            target_directory_path="/batch-archive/new",
        )
        moved_read = _read_file_data(
            client, kb_code=kb_code, file_path="/batch-source/source.md"
        )
        moved_search = _search_chunk_texts(
            client, kb_code=kb_code, query="batch move unique"
        )

        invalid_move = client.post(
            "/api/v1/knowledgeItems/move",
            json={
                "knCode": kb_code,
                "sourcePath": ["/batch-archive"],
                "targetDirectoryPath": "/batch-archive/new/loop",
            },
        )
        after_invalid_read = _read_file_data(
            client, kb_code=kb_code, file_path="/batch-source/source.md"
        )

    assert [item["targetPath"] for item in moved] == [
        "/batch-archive/new/a.md",
        "/batch-archive/new/b.md",
    ]
    assert "(/batch-archive/new/a.md)" in moved_read
    assert "(/batch-archive/new/b.md)" in moved_read
    assert any("(/batch-archive/new/a.md)" in text for text in moved_search)
    assert any("(/batch-archive/new/b.md)" in text for text in moved_search)
    assert invalid_move.status_code == 200
    assert invalid_move.json()["resultCode"] == "-1"
    assert after_invalid_read == moved_read
    assert "byqa-ref://" not in after_invalid_read


@pytest.mark.integration
async def test_reference_normalization_and_chunk_boundaries_do_not_leak_tokens(
    monkeypatch, tmp_path
):
    """Stable refs should normalize paths and survive real chunk splitting."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch,
        LocalEmbeddingDocumentChunkingService(chunk_size=20, chunk_overlap=0),
    )
    await _set_search_service(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/norm/source.md",
            file_content=(
                b"prefix [later](./b%20file.md#intro) suffix\n"
                b"escape [bad](../../outside.md)\n"
                b"normalization unique alpha\n"
            ),
        )
        read_before_target = _read_file_data(
            client, kb_code=kb_code, file_path="/norm/source.md"
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/norm/b file.md",
            file_content=b"# intro\n",
        )
        read_after_target = _read_file_data(
            client, kb_code=kb_code, file_path="/norm/source.md"
        )
        search_after_target = _search_chunk_texts(
            client, kb_code=kb_code, query="normalization unique alpha"
        )
        outbound_refs = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/norm/source.md",
            direction="outbound",
        )

    assert "(/norm/b file.md#intro)" in read_before_target
    assert "(/norm/b file.md#intro)" in read_after_target
    assert "(../../outside.md)" in read_after_target
    assert any("(/norm/b file.md#intro)" in text for text in search_after_target)
    assert all("byqa-ref://" not in text for text in search_after_target)
    assert all("b%20file.md" not in text for text in search_after_target)
    assert outbound_refs["outbound"] == [
        {
            "sourcePath": "/norm/source.md",
            "originalTarget": "/norm/b file.md",
            "targetSuffix": "",
            "targetPath": "/norm/b file.md",
            "status": "resolved",
        }
    ]


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
def test_zip_references_and_directory_delete_update_inbound_reference_state(
    monkeypatch, tmp_path
):
    """Zip imports should resolve internal refs; subtree delete should break inbound refs."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("b.md", b"# zip b\n")
        zf.writestr("a.md", b"zip [b](b.md)\nzip unique reference\n")

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        zip_response = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/zip"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        build_zip_a = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": "/zip/a.md"},
        )
        zip_read = _read_file_data(client, kb_code=kb_code, file_path="/zip/a.md")
        zip_refs = _reference_rows(client, kb_code=kb_code, target_path="/zip/b.md")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/delete-targets/b1.md",
            file_content=b"# b1\n",
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/delete-targets/sub/b2.md",
            file_content=b"# b2\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/delete-sources/s1.md",
            file_content=b"s1 [b1](../delete-targets/b1.md)\ndelete unique one\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/delete-sources/s2.md",
            file_content=b"s2 [b2](../delete-targets/sub/b2.md)\ndelete unique two\n",
        )
        before_delete_refs = _reference_rows(
            client, kb_code=kb_code, target_path="/delete-targets/sub/b2.md"
        )

        delete_dir = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/delete-targets"},
        )
        s1_after_delete = _read_file_data(
            client, kb_code=kb_code, file_path="/delete-sources/s1.md"
        )
        s2_after_delete = _read_file_data(
            client, kb_code=kb_code, file_path="/delete-sources/s2.md"
        )
        b1_broken_refs = _reference_rows(
            client, kb_code=kb_code, target_path="/delete-targets/b1.md"
        )
        b2_broken_refs = _reference_rows(
            client, kb_code=kb_code, target_path="/delete-targets/sub/b2.md"
        )

    assert zip_response.status_code == 200
    assert all(item["success"] for item in zip_response.json()["resultObject"]["data"])
    assert build_zip_a.status_code == 200
    assert "(/zip/b.md)" in zip_read
    assert "byqa-ref://" not in zip_read
    assert zip_refs == [
        {
            "sourcePath": "/zip/a.md",
            "originalTarget": "/zip/b.md",
            "targetSuffix": "",
            "targetPath": "/zip/b.md",
            "status": "resolved",
        }
    ]

    assert before_delete_refs[0]["status"] == "resolved"
    assert delete_dir.status_code == 200
    assert "(/delete-targets/b1.md)" in s1_after_delete
    assert before_delete_refs[0]["targetPath"] == "/delete-targets/sub/b2.md"
    assert "(/delete-targets/sub/b2.md)" in s2_after_delete
    assert "byqa-ref://" not in s1_after_delete
    assert "byqa-ref://" not in s2_after_delete
    assert b1_broken_refs == [
        {
            "sourcePath": "/delete-sources/s1.md",
            "originalTarget": "/delete-targets/b1.md",
            "targetSuffix": "",
            "targetPath": "/delete-targets/b1.md",
            "status": "broken",
        }
    ]
    assert b2_broken_refs == [
        {
            "sourcePath": "/delete-sources/s2.md",
            "originalTarget": "/delete-targets/sub/b2.md",
            "targetSuffix": "",
            "targetPath": "/delete-targets/sub/b2.md",
            "status": "broken",
        }
    ]


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
    downloaded_metadata, downloaded_body = split_front_matter(download.content)
    assert downloaded_metadata == {"documentKind": "original"}
    assert downloaded_body == body


@pytest.mark.integration
def test_import_zip_md_front_matter_is_persisted(monkeypatch, tmp_path):
    """A md inside a zip has its YAML front matter parsed and metadata persisted (processFrontMatter)."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    md = b"---\ntitle: ZipDoc\n---\n# body\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.md", md)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        meta = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": "/target/doc.md",
                "metadataFieldList": ["title"],
            },
        )

    assert resp.status_code == 200
    item = [
        d
        for d in resp.json()["resultObject"]["data"]
        if d["filePath"] == "/target/doc.md"
    ][0]
    assert item["success"] is True
    assert meta.status_code == 200
    metadata = meta.json()["resultObject"]["metadata"]
    assert metadata["title"] == {"valueType": "string", "value": "ZipDoc"}


@pytest.mark.integration
def test_import_zip_rejects_oversized_entry_at_route(monkeypatch, tmp_path):
    """A zip entry exceeding the per-entry decompressed cap is rejected at the route; no file is created."""
    import io
    import zipfile

    from by_qa.knowledge_base.services import zip_batch_import_service as zbmod

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    monkeypatch.setattr(zbmod, "_MAX_ENTRY_UNCOMPRESSED", 512)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("big.md", b"x" * 2048)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/target/big.md"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["resultCode"] == "-1"
    assert payload["resultMsg"] == "zip too large"
    # no file created
    assert download.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_import_zip_concurrent_many_files_all_succeed(monkeypatch, tmp_path):
    """A zip with 8 pngs + 8 mds (each md referencing its png) all upload successfully under 8-way concurrency."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    png_bytes = b"\x89PNG\r\n\x1a\npayload-\x00\x01"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(8):
            zf.writestr(f"img/{i}.png", png_bytes)
        for i in range(8):
            zf.writestr(f"{i}.md", f"![a](img/{i}.png)\n".encode("utf-8"))
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/target"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        md0_download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/target/0.md"},
        )
        png0_download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/target/img/0.png"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    data = payload["resultObject"]["data"]
    assert payload["resultObject"]["summary"]["succeeded"] == 16
    assert len(data) == 16
    assert all(d["success"] for d in data)
    # spot-check: md 0 reference rewritten to KB-absolute path
    assert b"![a](/target/img/0.png)" in md0_download.content
    # spot-check: png 0 bytes intact
    assert png0_download.content == png_bytes


@pytest.mark.integration
def test_import_zip_rewrites_references_when_target_exists(monkeypatch, tmp_path):
    """References resolve to existing KB targets (in same zip) and are rewritten to KB-absolute paths.

    Variants: a `../` relative image (stays within KB root), a link (non-image) form to a
    sibling md, and an anchor-suffixed image (anchor preserved).
    """
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    png_bytes = b"\x89PNG\r\n\x1a\nimg payload"
    doc = b"![a](../img/x.png)\n[link](../other.md)\n![frag](../img/x.png#section)\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("img/x.png", png_bytes)
        zf.writestr("other.md", b"# other\n")
        zf.writestr("sub/doc.md", doc)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/t"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/t/sub/doc.md"},
        )

    assert resp.status_code == 200
    assert all(d["success"] for d in resp.json()["resultObject"]["data"])
    content = download.content
    assert b"![a](/t/img/x.png)" in content
    assert b"[link](/t/other.md)" in content
    assert b"![frag](/t/img/x.png#section)" in content


@pytest.mark.integration
def test_import_zip_normalizes_unresolved_internal_references(monkeypatch, tmp_path):
    """Missing internal targets are normalized while skipped references stay unchanged."""
    import io
    import zipfile

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# t\n")
    )

    doc = (
        b"![miss](missing.png)\n"
        b"![ext](https://host/x.png)\n"
        b"[anchor](#section)\n"
        b"![esc](../../../x.png)\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.md", doc)
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": "/t"},
            files={"fileContent": ("batch.zip", buf.getvalue(), "application/zip")},
        )
        download = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/t/doc.md"},
        )

    assert resp.status_code == 200
    assert all(d["success"] for d in resp.json()["resultObject"]["data"])
    downloaded_metadata, downloaded_body = split_front_matter(download.content)
    assert downloaded_metadata == {"documentKind": "original"}
    assert downloaded_body == (
        b"![miss](/t/missing.png)\n"
        b"![ext](https://host/x.png)\n"
        b"[anchor](#section)\n"
        b"![esc](../../../x.png)\n"
    )


@pytest.mark.integration
async def test_document_update_markdown_replaces_content_and_invalidates_derived_state(
    monkeypatch, tmp_path
):
    """Updating Markdown clears its build state but retains absent front-matter values."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())
    await _set_search_service(monkeypatch, settings)

    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm_summary(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(
        MarkdownUpdateSummaryService, "generate_llm_summary", no_llm_summary
    )

    old = b"---\ntitle: Before\nowner: Platform\n---\n# Before\nold-only-token\n"
    new = b"---\ntitle: After\n---\n# After\nnew-only-token\n"
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        _upload_and_build_file(
            client, kb_code=kb_code, file_path="/docs/guide.md", file_content=old
        )
        assert any(
            "old-only-token" in text
            for text in _search_chunk_texts(
                client, kb_code=kb_code, query="old-only-token"
            )
        )

        response = _update_file(
            client, kb_code=kb_code, file_path="/docs/guide.md", file_content=new
        )
        raw_bytes = _download_file_bytes(
            client, kb_code=kb_code, file_path="/docs/guide.md"
        )
        build_status = _file_build_status(
            client, kb_code=kb_code, file_path="/docs/guide.md"
        )
        metadata = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": "/docs/guide.md",
                "metadataFieldList": ["title", "owner"],
            },
        )
        search_after = _search_chunk_texts(
            client, kb_code=kb_code, query="new-only-token"
        )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "0"
    downloaded_metadata, downloaded_body = split_front_matter(raw_bytes)
    assert downloaded_metadata == {
        "title": "After",
        "owner": "Platform",
        "documentKind": "original",
    }
    assert downloaded_body == b"# After\nnew-only-token\n"
    assert build_status.json()["resultCode"] == "-1"
    assert "build task not found" in build_status.json()["resultMsg"]
    assert search_after == []
    assert metadata.json()["resultObject"]["metadata"] == {
        "title": {"valueType": "string", "value": "After"},
        "owner": {"valueType": "string", "value": "Platform"},
    }


@pytest.mark.integration
async def test_document_update_markdown_reregisters_stable_source_references_and_timeline(
    monkeypatch, tmp_path
):
    """A Markdown update keeps its reference graph and emits a timeline event without building."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm_summary(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(
        MarkdownUpdateSummaryService, "generate_llm_summary", no_llm_summary
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/assets/logo.png",
            file_content=b"png",
            content_type="image/png",
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/assets/new-logo.png",
            file_content=b"new-png",
            content_type="image/png",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/docs/source.md",
            file_content=b"# Old\n![logo](../assets/logo.png)\n",
        )
        response = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/source.md",
            file_content=b"# New\n![logo](../assets/new-logo.png)\n",
        )
        references = _reference_result(
            client,
            kb_code=kb_code,
            file_path="/docs/source.md",
            direction="outbound",
        )
        build_status = _file_build_status(
            client, kb_code=kb_code, file_path="/docs/source.md"
        )

    timeline = await _latest_update_timeline(
        settings, kb_code=kb_code, file_path="/docs/source.md"
    )
    assert response.status_code == 200
    assert response.json()["resultCode"] == "0"
    assert references["outbound"] == [
        {
            "sourcePath": "/docs/source.md",
            "originalTarget": "/assets/new-logo.png",
            "targetSuffix": "",
            "targetPath": "/assets/new-logo.png",
            "status": "resolved",
        }
    ]
    assert build_status.json()["resultCode"] == "-1"
    assert timeline["old_file_size"] > 0
    assert timeline["new_file_size"] > 0
    assert timeline["summary_source"] in {"RULE_BASED", "LLM"}


@pytest.mark.integration
async def test_document_update_non_markdown_and_validation_errors_use_http_200_envelope(
    monkeypatch, tmp_path
):
    """Non-Markdown updates avoid LLM work; malformed updates retain the public error envelope."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    llm_calls: list[tuple[str, str]] = []

    async def unexpected_llm_summary(self, old_markdown, new_markdown):
        _ = self
        llm_calls.append((old_markdown, new_markdown))
        raise AssertionError("non-Markdown document updates must not invoke the LLM")

    monkeypatch.setattr(
        MarkdownUpdateSummaryService,
        "generate_llm_summary",
        unexpected_llm_summary,
    )

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/assets/logo.png",
            file_content=b"old-png",
            content_type="image/png",
        )
        success = _update_file(
            client,
            kb_code=kb_code,
            file_path="/assets/logo.png",
            file_content=b"new-png",
            content_type="image/png",
        )
        await _create_running_build_task(settings, kb_code=kb_code, name="logo.png")
        zip_error = _update_file(
            client,
            kb_code=kb_code,
            file_path="/assets/logo.png",
            file_content=b"zip",
            upload_name="batch.zip",
            content_type="application/zip",
        )
        suffix_error = _update_file(
            client,
            kb_code=kb_code,
            file_path="/assets/logo.png",
            file_content=b"text",
            upload_name="logo.md",
        )
        missing_error = _update_file(
            client,
            kb_code=kb_code,
            file_path="/assets/missing.png",
            file_content=b"missing",
            content_type="image/png",
        )
        running_error = _update_file(
            client,
            kb_code=kb_code,
            file_path="/assets/logo.png",
            file_content=b"blocked",
            content_type="image/png",
        )
        logo_bytes = _download_file_bytes(
            client, kb_code=kb_code, file_path="/assets/logo.png"
        )

    assert success.status_code == 200
    assert success.json()["resultCode"] == "0"
    assert logo_bytes == b"new-png"
    assert llm_calls == []
    for response in (zip_error, suffix_error, missing_error, running_error):
        assert response.status_code == 200
        assert response.json()["resultCode"] == "-1"
    assert (
        "File is being built and cannot be updated" in running_error.json()["resultMsg"]
    )


@pytest.mark.integration
async def test_document_update_markdown_backfill_uses_llm_summary_and_keeps_fallback_on_failure(
    monkeypatch, tmp_path
):
    """The background task persists a usable LLM summary but leaves rule fallback on failure."""
    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    async def llm_summary(self, old_markdown, new_markdown):
        _ = self
        assert "Old" in old_markdown and "New" in new_markdown
        return "这是由模型生成的、足够长的文档更新摘要，用于验证后台回填能够持久化。"

    monkeypatch.setattr(
        MarkdownUpdateSummaryService, "generate_llm_summary", llm_summary
    )
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Integration KB {uuid4().hex[:12]}")
        _upload_file(
            client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Old\n"
        )
        first = _update_file(
            client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# New\n"
        )

    llm_timeline = await _latest_update_timeline(
        settings, kb_code=kb_code, file_path="/docs/a.md"
    )

    async def failed_llm_summary(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        raise RuntimeError("simulated LLM outage")

    monkeypatch.setattr(
        MarkdownUpdateSummaryService, "generate_llm_summary", failed_llm_summary
    )
    with TestClient(main_module.app) as client:
        second = _update_file(
            client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Newer\n"
        )

    fallback_timeline = await _latest_update_timeline(
        settings, kb_code=kb_code, file_path="/docs/a.md"
    )
    assert first.json()["resultCode"] == "0"
    assert llm_timeline["summary_source"] == "LLM"
    assert llm_timeline["summary"].startswith("这是由模型生成")
    assert second.json()["resultCode"] == "0"
    assert fallback_timeline["summary_source"] == "RULE_BASED"


@pytest.mark.integration
async def test_metadata_pagination_system_signatures_and_file_guards(
    monkeypatch, tmp_path
):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata guards {uuid4().hex[:12]}")
        for path, content in (
            ("/docs/first.md", b"# First\n"),
            ("/docs/second.md", b"# Second\n"),
            ("/docs/third.md", b"# Third\n"),
        ):
            _upload_file(
                client,
                kb_code=kb_code,
                file_path=path,
                file_content=content,
            )

        page_response = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"exists": {"fieldName": "fileSignature"}},
                "metadataFieldList": [
                    "createdAt",
                    "updatedAt",
                    "fileSignature",
                ],
                "pageNum": 1,
                "pageSize": 2,
            },
        )
        page = page_response.json()["resultObject"]
        assert page["total"] == 3
        assert page["pageNum"] == 1
        assert page["pageSize"] == 2
        assert [item["filePath"] for item in page["data"]] == [
            "/docs/first.md",
            "/docs/second.md",
        ]
        first_metadata = page["data"][0]["metadata"]
        assert first_metadata["createdAt"]["value"]
        assert first_metadata["updatedAt"]["value"]
        signature = first_metadata["fileSignature"]["value"]

        metadata_response = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": "/docs/first.md",
                "metadataFieldList": [
                    "createdAt",
                    "updatedAt",
                    "fileSignature",
                ],
            },
        )
        assert metadata_response.json()["resultObject"]["metadata"] == first_metadata

        duplicate_import = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/docs/duplicate.md",
                "skipIfDuplicate": "true",
            },
            files={"fileContent": ("duplicate.md", b"# First\n", "text/markdown")},
        )
        assert duplicate_import.json()["resultCode"] == "-1"
        assert "/docs/first.md" in duplicate_import.json()["resultMsg"]

        stale_update = client.post(
            "/api/v1/knowledgeItems/update",
            data={
                "knCode": kb_code,
                "filePath": "/docs/first.md",
                "referSignature": "stale",
            },
            files={"fileContent": ("first.md", b"# Updated\n", "text/markdown")},
        )
        assert stale_update.json()["resultCode"] == "-1"
        assert "file signature mismatch" in stale_update.json()["resultMsg"]

        duplicate_update = client.post(
            "/api/v1/knowledgeItems/update",
            data={
                "knCode": kb_code,
                "filePath": "/docs/first.md",
                "referSignature": signature,
                "skipIfDuplicate": "true",
            },
            files={"fileContent": ("first.md", b"# Second\n", "text/markdown")},
        )
        assert duplicate_update.json()["resultCode"] == "-1"
        assert "/docs/second.md" in duplicate_update.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_search_uses_unchanged_request_for_files_and_directories(
    monkeypatch, tmp_path
):
    """One existing request shape returns mixed entries and response-only type labels."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata mixed {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/catalog",
            metadata={"scope": "shared", "directoryOnly": "yes"},
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/catalog/item.md",
            file_content=b"# Item\n",
        )
        assert (
            _update_file_metadata(
                client,
                kb_code=kb_code,
                file_path="/catalog/item.md",
                operation_list=[
                    {
                        "propertyName": "scope",
                        "operation": "set",
                        "valueType": "string",
                        "value": "shared",
                    }
                ],
            ).json()["resultCode"]
            == "0"
        )

        def search(where: dict, *, page_num: int = 1, page_size: int = 20) -> dict:
            payload = client.post(
                "/api/v1/knowledgeItems/metadataSearch",
                json={
                    "knCodeList": [kb_code],
                    "where": where,
                    "metadataFieldList": [
                        "scope",
                        "fileName",
                        "fileType",
                        "fileSize",
                        "mimeType",
                        "fileSignature",
                        "filePath",
                    ],
                    "pageNum": page_num,
                    "pageSize": page_size,
                },
            ).json()
            assert payload["resultCode"] == "0", payload
            return payload["resultObject"]

        mixed = search({"eq": {"fieldName": "scope", "value": "shared"}})
        first_page = search(
            {"eq": {"fieldName": "scope", "value": "shared"}}, page_size=1
        )
        second_page = search(
            {"eq": {"fieldName": "scope", "value": "shared"}},
            page_num=2,
            page_size=1,
        )
        directory_only = search({"eq": {"fieldName": "directoryOnly", "value": "yes"}})
        directory_updated_at = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/catalog",
            field_names=["updatedAt"],
        )["updatedAt"]["value"]
        exact_directory_name = search(
            {"eq": {"fieldName": "fileName", "value": "catalog"}}
        )
        exact_directory = search({"eq": {"fieldName": "filePath", "value": "/catalog"}})
        exact_directory_update = search(
            {"eq": {"fieldName": "updatedAt", "value": directory_updated_at}}
        )

        renamed = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/catalog",
                "directoryName": "renamed",
            },
        ).json()
        after_rename = search({"eq": {"fieldName": "scope", "value": "shared"}})
        deleted = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/renamed"},
        ).json()
        after_delete = search({"eq": {"fieldName": "scope", "value": "shared"}})

    assert mixed["total"] == 2
    by_type = {item["type"]: item for item in mixed["data"]}
    assert set(by_type) == {"directory", "file"}
    assert by_type["directory"]["filePath"] == "/catalog"
    assert by_type["file"]["filePath"] == "/catalog/item.md"
    assert by_type["directory"]["metadata"] == {
        "scope": {"valueType": "string", "value": "shared"},
        "fileName": {"valueType": "string", "value": "catalog"},
        "fileType": {"valueType": "string", "value": ""},
        "fileSize": {"valueType": "number", "value": 0},
        "mimeType": {"valueType": "string", "value": None},
        "fileSignature": {"valueType": "string", "value": None},
        "filePath": {"valueType": "string", "value": "/catalog"},
    }
    assert first_page["total"] == second_page["total"] == 2
    paged = first_page["data"] + second_page["data"]
    assert paged == mixed["data"]
    assert {item["type"] for item in paged} == {"directory", "file"}
    assert directory_only["total"] == 1
    assert directory_only["data"][0]["type"] == "directory"
    assert exact_directory_name["total"] == 1
    assert exact_directory_name["data"][0]["type"] == "directory"
    assert exact_directory["total"] == 1
    assert exact_directory["data"][0]["type"] == "directory"
    assert exact_directory_update["total"] == 1
    assert exact_directory_update["data"][0]["type"] == "directory"
    assert renamed["resultCode"] == "0"
    assert {item["filePath"] for item in after_rename["data"]} == {
        "/renamed",
        "/renamed/item.md",
    }
    assert deleted["resultCode"] == "0"
    assert after_delete["total"] == 0
    assert after_delete["data"] == []


@pytest.mark.integration
async def test_metadata_search_uses_entry_id_to_break_equal_timestamp_ties(
    monkeypatch, tmp_path
):
    """Mixed metadataSearch results remain stable when updatedAt values tie."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata tie {uuid4().hex[:12]}")
        _create_directory(
            client,
            kb_code=kb_code,
            directory_path="/tie",
            metadata={"tieScope": "shared"},
        )
        response = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kb_code,
                "filePath": "/tie/file.md",
                "metadata": '{"tieScope":"shared"}',
            },
            files={"fileContent": ("file.md", b"# Tie\n", "text/markdown")},
        )
        assert response.json()["resultCode"] == "0", response.json()

        connection = await build_connection_factory(settings)()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                UPDATE knowledge_fs_entry
                SET updated_at = %(updated_at)s
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND virtual_path = ANY(%(paths)s)
                  AND is_deleted = false
                """,
                {
                    "knowledge_base_id": int(kb_code),
                    "paths": ["/tie", "/tie/file.md"],
                    "updated_at": "2026-08-30T00:00:00+08:00",
                },
            )
            await cursor.execute(
                """
                SELECT kid, virtual_path
                FROM knowledge_fs_entry
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND virtual_path = ANY(%(paths)s)
                  AND is_deleted = false
                ORDER BY kid
                """,
                {
                    "knowledge_base_id": int(kb_code),
                    "paths": ["/tie", "/tie/file.md"],
                },
            )
            expected_paths = [row["virtual_path"] for row in await cursor.fetchall()]
            await connection.commit()
        finally:
            await connection.close()

        result = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"eq": {"fieldName": "tieScope", "value": "shared"}},
                "pageNum": 1,
                "pageSize": 1,
            },
        ).json()["resultObject"]
        second_page = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"eq": {"fieldName": "tieScope", "value": "shared"}},
                "pageNum": 2,
                "pageSize": 1,
            },
        ).json()["resultObject"]

    assert result["total"] == second_page["total"] == 2
    assert [
        result["data"][0]["filePath"],
        second_page["data"][0]["filePath"],
    ] == expected_paths


@pytest.mark.integration
async def test_metadata_search_paginates_and_sorts_by_updated_at_ascending(
    monkeypatch, tmp_path
):
    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(MarkdownUpdateSummaryService, "generate_llm_summary", no_llm)
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Metadata paging {uuid4().hex[:12]}")
        for path in ("/docs/first.md", "/docs/second.md", "/docs/third.md"):
            _upload_file(
                client,
                kb_code=kb_code,
                file_path=path,
                file_content=f"# {path}\n".encode(),
            )

        def search_page(page_num: int) -> dict:
            response = client.post(
                "/api/v1/knowledgeItems/metadataSearch",
                json={
                    "knCodeList": [kb_code],
                    "where": {"exists": {"fieldName": "fileSignature"}},
                    "pageNum": page_num,
                    "pageSize": 2,
                },
            )
            payload = response.json()
            assert payload["resultCode"] == "0", payload
            return payload["resultObject"]

        first_page = search_page(1)
        second_page = search_page(2)
        assert first_page["total"] == second_page["total"] == 3
        assert [item["filePath"] for item in first_page["data"]] == [
            "/docs/first.md",
            "/docs/second.md",
        ]
        assert [item["filePath"] for item in second_page["data"]] == ["/docs/third.md"]

        update_response = _update_file(
            client,
            kb_code=kb_code,
            file_path="/docs/first.md",
            file_content=b"# First updated\n",
        )
        assert update_response.json()["resultCode"] == "0"

        reordered_first_page = search_page(1)
        reordered_second_page = search_page(2)
        empty_page = search_page(3)
        assert [item["filePath"] for item in reordered_first_page["data"]] == [
            "/docs/second.md",
            "/docs/third.md",
        ]
        assert [item["filePath"] for item in reordered_second_page["data"]] == [
            "/docs/first.md"
        ]
        assert empty_page["total"] == 3
        assert empty_page["data"] == []


@pytest.mark.integration
async def test_refer_signature_success_stale_rejection_and_exact_metadata_filter(
    monkeypatch, tmp_path
):
    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(MarkdownUpdateSummaryService, "generate_llm_summary", no_llm)
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"Refer signature {uuid4().hex[:12]}")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            file_content=b"# Old\n",
        )
        old_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            field_names=["updatedAt", "fileSignature"],
        )
        old_signature = old_metadata["fileSignature"]["value"]

        exact_match = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {
                    "eq": {
                        "fieldName": "fileSignature",
                        "value": old_signature,
                    }
                },
                "metadataFieldList": ["fileSignature"],
                "pageNum": 1,
                "pageSize": 10,
            },
        ).json()
        assert exact_match["resultCode"] == "0"
        assert [item["filePath"] for item in exact_match["resultObject"]["data"]] == [
            "/docs/a.md"
        ]

        successful_update = client.post(
            "/api/v1/knowledgeItems/update",
            data={
                "knCode": kb_code,
                "filePath": "/docs/a.md",
                "referSignature": old_signature,
            },
            files={"fileContent": ("a.md", b"# New\n", "text/markdown")},
        )
        assert successful_update.json()["resultCode"] == "0"
        new_metadata = _get_file_metadata(
            client,
            kb_code=kb_code,
            file_path="/docs/a.md",
            field_names=["updatedAt", "fileSignature"],
        )
        assert new_metadata["fileSignature"]["value"] != old_signature
        assert new_metadata["updatedAt"]["value"] >= old_metadata["updatedAt"]["value"]
        downloaded_metadata, downloaded_body = split_front_matter(
            _download_file_bytes(client, kb_code=kb_code, file_path="/docs/a.md")
        )
        assert downloaded_metadata == {"documentKind": "original"}
        assert downloaded_body == b"# New\n"

        stale_update = client.post(
            "/api/v1/knowledgeItems/update",
            data={
                "knCode": kb_code,
                "filePath": "/docs/a.md",
                "referSignature": old_signature,
            },
            files={"fileContent": ("a.md", b"# Stale\n", "text/markdown")},
        )
        assert stale_update.json()["resultCode"] == "-1"
        downloaded_metadata, downloaded_body = split_front_matter(
            _download_file_bytes(client, kb_code=kb_code, file_path="/docs/a.md")
        )
        assert downloaded_metadata == {"documentKind": "original"}
        assert downloaded_body == b"# New\n"
        assert (
            _get_file_metadata(
                client,
                kb_code=kb_code,
                file_path="/docs/a.md",
                field_names=["fileSignature"],
            )["fileSignature"]["value"]
            == new_metadata["fileSignature"]["value"]
        )


@pytest.mark.integration
async def test_duplicate_checksum_flag_scope_zip_and_self_update(monkeypatch, tmp_path):
    import io
    import zipfile

    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(MarkdownUpdateSummaryService, "generate_llm_summary", no_llm)
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    _set_document_chunking_service(monkeypatch, EchoDocumentChunkingService())

    with TestClient(main_module.app) as client:
        first_kb = _create_kb(client, f"Duplicate A {uuid4().hex[:12]}")
        second_kb = _create_kb(client, f"Duplicate B {uuid4().hex[:12]}")
        content = b"# Same\n"
        _upload_file(
            client,
            kb_code=first_kb,
            file_path="/docs/source.md",
            file_content=content,
        )

        allowed_duplicate = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": first_kb, "filePath": "/docs/allowed.md"},
            files={"fileContent": ("allowed.md", content, "text/markdown")},
        )
        assert allowed_duplicate.json()["resultCode"] == "0"

        rejected_duplicate = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": first_kb,
                "filePath": "/docs/rejected.md",
                "skipIfDuplicate": "true",
            },
            files={"fileContent": ("rejected.md", content, "text/markdown")},
        )
        assert rejected_duplicate.json()["resultCode"] == "-1"
        rejected_metadata = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": first_kb, "filePath": "/docs/rejected.md"},
        ).json()
        assert rejected_metadata["resultCode"] == "-1"

        cross_kb_duplicate = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": second_kb,
                "filePath": "/docs/source.md",
                "skipIfDuplicate": "true",
            },
            files={"fileContent": ("source.md", content, "text/markdown")},
        )
        assert cross_kb_duplicate.json()["resultCode"] == "0"

        second_signature = _get_file_metadata(
            client,
            kb_code=second_kb,
            file_path="/docs/source.md",
            field_names=["fileSignature"],
        )["fileSignature"]["value"]
        self_update = client.post(
            "/api/v1/knowledgeItems/update",
            data={
                "knCode": second_kb,
                "filePath": "/docs/source.md",
                "referSignature": second_signature,
                "skipIfDuplicate": "true",
            },
            files={"fileContent": ("source.md", content, "text/markdown")},
        )
        assert self_update.json()["resultCode"] == "0"

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("zip-duplicate.md", content)
        zip_duplicate = client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": first_kb,
                "filePath": "/docs",
                "skipIfDuplicate": "true",
            },
            files={
                "fileContent": (
                    "duplicates.zip",
                    archive.getvalue(),
                    "application/zip",
                )
            },
        ).json()
        assert zip_duplicate["resultCode"] == "0"
        assert zip_duplicate["resultObject"]["summary"] == {
            "total": 1,
            "succeeded": 0,
            "failed": 1,
        }
        assert (
            "duplicate file checksum"
            in zip_duplicate["resultObject"]["data"][0]["error"]
        )


@pytest.mark.integration
async def test_checksum_advisory_lock_blocks_same_scope_only(monkeypatch, tmp_path):
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    connection_factory = build_connection_factory(settings)
    first_connection = await connection_factory()
    second_connection = await connection_factory()
    repository = KnowledgeFsEntryRepository()
    blocked_task = None
    try:
        first_cursor = first_connection.cursor()
        second_cursor = second_connection.cursor()
        await repository.lock_checksum_scope(
            first_cursor,
            knowledge_base_id=91001,
            checksum="same-checksum",
        )
        blocked_task = asyncio.create_task(
            repository.lock_checksum_scope(
                second_cursor,
                knowledge_base_id=91001,
                checksum="same-checksum",
            )
        )
        _, pending = await asyncio.wait({blocked_task}, timeout=0.2)
        assert blocked_task in pending

        await first_connection.rollback()
        await asyncio.wait_for(blocked_task, timeout=2)
        await second_connection.rollback()

        await repository.lock_checksum_scope(
            first_cursor,
            knowledge_base_id=91001,
            checksum="checksum-a",
        )
        await asyncio.wait_for(
            repository.lock_checksum_scope(
                second_cursor,
                knowledge_base_id=91001,
                checksum="checksum-b",
            ),
            timeout=1,
        )
        await first_connection.rollback()
        await second_connection.rollback()

        await repository.lock_checksum_scope(
            first_cursor,
            knowledge_base_id=91001,
            checksum="cross-kb",
        )
        await asyncio.wait_for(
            repository.lock_checksum_scope(
                second_cursor,
                knowledge_base_id=91002,
                checksum="cross-kb",
            ),
            timeout=1,
        )
    finally:
        if blocked_task is not None and not blocked_task.done():
            blocked_task.cancel()
        await first_connection.rollback()
        await second_connection.rollback()
        await first_connection.close()
        await second_connection.close()
