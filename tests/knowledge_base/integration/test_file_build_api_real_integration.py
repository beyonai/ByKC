"""HTTP-to-worker integration coverage for durable file Build batches."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import sql

import by_qa.main as main_module
from by_qa.config import get_settings
from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_base.events import KnowledgeEventPublisherInvoker
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

pytestmark = pytest.mark.integration


class _EmbeddingConfigProvider:
    def __init__(self, *, dimension: int):
        self.dimension = dimension

    async def get_config(self, model_type: str) -> ModelConfig:
        assert model_type == "embedding"
        return ModelConfig(
            model_name="file-build-api-integration",
            temperature=0.0,
            base_url="https://embedding.invalid",
            api_key="integration-test",
            dimension=self.dimension,
            distance_metric="cosine",
        )


class _EchoChunkingService:
    def __init__(self, *, dimension: int):
        self.dimension = dimension

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        assert file_type == "txt"
        return file_bytes.decode("utf-8")

    def chunk_and_embed(
        self, markdown_bytes: bytes, *, filename: str
    ) -> list[KnowledgeItemChunkPayload]:
        assert filename.endswith(".md")
        text = markdown_bytes.decode("utf-8")
        return [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=max(1, len(text.splitlines())),
                chunk_text=text,
                embedding=[0.1] * self.dimension,
            )
        ]


class _RecordingPublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


def _reset_main_runtime(
    monkeypatch: pytest.MonkeyPatch, *, settings, publisher
) -> None:
    provider = _EmbeddingConfigProvider(dimension=settings.embedding_dimension)
    chunking_service = _EchoChunkingService(dimension=settings.embedding_dimension)
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "load_model_config_provider", lambda: provider)

    async def _get_chunker(provider=None):  # pylint: disable=unused-argument
        return chunking_service

    monkeypatch.setattr(
        main_module, "_get_or_build_document_chunking_service", _get_chunker
    )
    for name in (
        "_knowledge_base_service",
        "_knowledge_item_ingestion_service",
        "_knowledge_item_search_service",
        "_document_update_service",
        "_knowledge_fetch_cache_cleanup_service",
        "_document_chunking_service",
        "_metadata_search_service",
        "_file_metadata_query_service",
        "_file_metadata_update_service",
        "_knowledge_entity_processing_service",
    ):
        monkeypatch.setattr(main_module, name, None)
    monkeypatch.setattr(
        main_module,
        "_knowledge_event_publisher_invoker",
        KnowledgeEventPublisherInvoker(publisher=publisher),
    )
    monkeypatch.setattr(main_module, "_knowledge_base_schema_initialized", False)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_lock", asyncio.Lock())

    async def _noop_register(application):  # pylint: disable=unused-argument
        return None

    monkeypatch.setattr(main_module, "_register_service", _noop_register)
    monkeypatch.setattr(main_module, "_unregister_service", _noop_register)


def _assert_success(response) -> dict:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]


def _wait_for_batch(client: TestClient, *, kb_code: str, batch_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = _assert_success(
            client.post(
                "/api/v1/knowledgeItems/processingBatchStatus",
                json={
                    "knCode": kb_code,
                    "batchId": batch_id,
                    "includeDetails": True,
                },
            )
        )
        if result["status"] == "COMPLETED":
            return result
        time.sleep(0.05)
    pytest.fail(f"file Build batch did not complete: {batch_id}")


async def _drop_schema(settings) -> None:
    connection = await build_connection_factory(settings)()
    try:
        await connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(settings.db_schema))
        )
        await connection.commit()
    finally:
        await connection.close()


def test_directory_build_runs_from_http_to_worker_and_reports_current_result(
    monkeypatch, tmp_path
):
    """Directory acceptance, worker completion, reuse and rebuild use public APIs."""
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    settings = base_settings.model_copy(
        update={
            "db_schema": f"file_build_api_it_{uuid4().hex[:16]}",
            "agent_data_path": tmp_path,
            "embedding_model_name": "file-build-api-integration",
            "embedding_dimension": 3,
            "knowledge_entity_worker_enabled": False,
            "knowledge_build_worker_enabled": True,
            "knowledge_build_worker_id": "file-build-api-test",
            "knowledge_build_worker_poll_seconds": 0.05,
            "knowledge_build_worker_concurrency": 16,
            "knowledge_build_lease_seconds": 10,
            "knowledge_build_heartbeat_seconds": 1.0,
            "knowledge_build_reaper_seconds": 0.1,
            "knowledge_build_worker_status_log_seconds": 60.0,
            "knowledge_build_shutdown_grace_seconds": 2.0,
        }
    )
    publisher = _RecordingPublisher()
    _reset_main_runtime(monkeypatch, settings=settings, publisher=publisher)
    kb_code = None
    try:
        with TestClient(main_module.app) as client:
            kb_code = _assert_success(
                client.post(
                    "/api/v1/knowledgeBases/create",
                    json={"knName": f"File Build API {uuid4().hex}"},
                )
            )["knCode"]
            _assert_success(
                client.post(
                    "/api/v1/directories/create",
                    json={"knCode": kb_code, "directoryPath": "/docs"},
                )
            )
            for name, content in (
                ("alpha.txt", b"alpha\nfirst"),
                ("beta.txt", b"beta\nsecond"),
            ):
                _assert_success(
                    client.post(
                        "/api/v1/knowledgeItems/import",
                        data={"knCode": kb_code, "filePath": f"/docs/{name}"},
                        files={"fileContent": (name, content, "text/plain")},
                    )
                )

            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": "/docs"},
                )
            )
            assert accepted == {
                **accepted,
                "scope": "DIRECTORY",
                "targetPath": "/docs",
                "taskType": "FILE_BUILD",
                "candidateCount": 2,
                "eligibleCount": 2,
                "acceptedCount": 2,
                "reusedCount": 0,
                "skippedCount": 0,
                "returnedTaskCount": 2,
                "tasksTruncated": False,
            }
            first_batch = _wait_for_batch(
                client, kb_code=kb_code, batch_id=accepted["batchId"]
            )
            assert first_batch["totalCount"] == 2
            assert first_batch["succeededCount"] == 2
            assert first_batch["completedCount"] == 2
            assert {item["status"] for item in first_batch["data"]} == {"SUCCEEDED"}

            first_result = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": "/docs/alpha.txt"},
                )
            )
            assert first_result["isBuilt"] is True
            assert first_result["build"]["status"] == "complete"
            assert (
                first_result["build"]["inputChecksum"]
                == first_result["currentChecksum"]
            )
            assert first_result["markdown"]["data"] == "alpha\nfirst"
            assert first_result["chunks"]["total"] == 1
            assert first_result["embedding"]["coverageRate"] == 100.0
            assert first_result["retrieval"]["coverageRate"] == 100.0

            reused = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": "/docs"},
                )
            )
            assert reused["acceptedCount"] == 0
            assert reused["reusedCount"] == 2
            reused_batch = _wait_for_batch(
                client, kb_code=kb_code, batch_id=reused["batchId"]
            )
            assert reused_batch["totalCount"] == 0
            assert reused_batch["completedCount"] == 0

            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/update",
                    data={"knCode": kb_code, "filePath": "/docs/alpha.txt"},
                    files={
                        "fileContent": (
                            "alpha.txt",
                            b"alpha changed\nnew checksum",
                            "text/plain",
                        )
                    },
                )
            )
            stale_result = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": "/docs/alpha.txt"},
                )
            )
            assert stale_result["isBuilt"] is False
            assert (
                stale_result["build"]["inputChecksum"]
                != stale_result["currentChecksum"]
            )
            assert stale_result["markdown"]["available"] is False
            assert stale_result["chunks"]["total"] == 0

            rebuilt = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": "/docs"},
                )
            )
            assert rebuilt["acceptedCount"] == 1
            assert rebuilt["reusedCount"] == 1
            rebuilt_batch = _wait_for_batch(
                client, kb_code=kb_code, batch_id=rebuilt["batchId"]
            )
            assert rebuilt_batch["succeededCount"] == 1
            assert (
                _assert_success(
                    client.post(
                        "/api/v1/buildResult",
                        json={"knCode": kb_code, "filePath": "/docs/alpha.txt"},
                    )
                )["isBuilt"]
                is True
            )

            build_events = [
                event
                for event in publisher.events
                if event.event_type.startswith("build.")
            ]
            file_events = [
                event
                for event in build_events
                if event.event_type == "build.file.completed"
            ]
            batch_events = [
                event
                for event in build_events
                if event.event_type == "build.batch.completed"
            ]
            assert len(file_events) == 3
            assert len(batch_events) == 3
            assert all(event.event_version == 2 for event in build_events)
    finally:
        if kb_code is not None:
            # Storage cleanup follows the same public deletion path used in production.
            with TestClient(main_module.app) as cleanup_client:
                _assert_success(
                    cleanup_client.post(
                        "/api/v1/knowledgeBases/delete", json={"knCode": kb_code}
                    )
                )
        asyncio.run(_drop_schema(settings))
