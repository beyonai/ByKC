"""HTTP-to-worker integration coverage for durable file Build batches."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import sql

import by_qa.knowledge_base.infrastructure.runtime as runtime_module
import by_qa.main as main_module
from by_qa.config import get_settings
from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_base.events import KnowledgeEventPublisherInvoker
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.services.file_build_processing_service import (
    FileBuildProcessingService,
)
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
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings,
    publisher,
    start_worker: bool = True,
) -> None:
    provider = _EmbeddingConfigProvider(dimension=settings.embedding_dimension)
    chunking_service = _EchoChunkingService(dimension=settings.embedding_dimension)
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "load_model_config_provider", lambda: provider)
    monkeypatch.setattr(
        runtime_module,
        "load_knowledge_event_publisher",
        lambda provider_path: publisher,
    )

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
    if not start_worker:

        async def _do_not_start_worker(self):  # pylint: disable=unused-argument
            return None

        monkeypatch.setattr(FileBuildProcessingService, "start", _do_not_start_worker)


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


def test_updated_file_returns_not_built_and_can_be_rebuilt(monkeypatch, tmp_path):
    """Updating file content invalidates the current Build until a rebuild succeeds."""
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    settings = base_settings.model_copy(
        update={
            "db_schema": f"file_build_update_it_{uuid4().hex[:16]}",
            "agent_data_path": tmp_path,
            "embedding_model_name": "file-build-api-integration",
            "embedding_dimension": 3,
            "knowledge_entity_worker_enabled": False,
            "knowledge_build_worker_enabled": True,
            "knowledge_build_worker_id": "file-build-update-test",
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
                    json={"knName": f"File Build Update {uuid4().hex}"},
                )
            )["knCode"]
            file_path = "/docs/update.txt"
            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/import",
                    data={"knCode": kb_code, "filePath": file_path},
                    files={
                        "fileContent": ("update.txt", b"before update", "text/plain")
                    },
                )
            )

            first_acceptance = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": file_path},
                )
            )
            first_batch = _wait_for_batch(
                client,
                kb_code=kb_code,
                batch_id=first_acceptance["batchId"],
            )
            assert first_batch["succeededCount"] == 1
            before_update = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": file_path},
                )
            )
            assert before_update["isBuilt"] is True
            assert before_update["markdown"]["data"] == "before update"

            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/update",
                    data={"knCode": kb_code, "filePath": file_path},
                    files={
                        "fileContent": ("update.txt", b"after update", "text/plain")
                    },
                )
            )
            after_update = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": file_path},
                )
            )
            assert after_update["fileId"] == before_update["fileId"]
            assert after_update["currentChecksum"] != before_update["currentChecksum"]
            assert after_update["isBuilt"] is False
            assert after_update["build"]["taskId"] == before_update["build"]["taskId"]
            assert (
                after_update["build"]["inputChecksum"]
                != after_update["currentChecksum"]
            )
            assert after_update["markdown"]["available"] is False
            assert after_update["chunks"]["total"] == 0
            listed_after_update = _assert_success(
                client.post(
                    "/api/v1/listDir",
                    json={"knCode": kb_code, "directoryPath": "/docs"},
                )
            )["data"][0]
            assert listed_after_update["name"] == file_path
            assert listed_after_update["buildStatus"] is None
            assert listed_after_update["buildCurrentStep"] is None
            status_after_update = client.post(
                "/api/v1/fileBuildStatus",
                json={"knCode": kb_code, "filePath": file_path},
            )
            assert status_after_update.status_code == 200
            assert status_after_update.json()["resultCode"] == "-1"
            assert "build task not found" in status_after_update.json()["resultMsg"]

            rebuild_acceptance = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": file_path},
                )
            )
            assert rebuild_acceptance["acceptedCount"] == 1
            assert rebuild_acceptance["reusedCount"] == 0
            assert (
                rebuild_acceptance["tasks"][0]["taskId"]
                != before_update["build"]["taskId"]
            )
            rebuilt_batch = _wait_for_batch(
                client,
                kb_code=kb_code,
                batch_id=rebuild_acceptance["batchId"],
            )
            assert rebuilt_batch["succeededCount"] == 1

            rebuilt = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": file_path},
                )
            )
            assert rebuilt["fileId"] == before_update["fileId"]
            assert rebuilt["isBuilt"] is True
            assert (
                rebuilt["build"]["taskId"] == rebuild_acceptance["tasks"][0]["taskId"]
            )
            assert rebuilt["build"]["inputChecksum"] == rebuilt["currentChecksum"]
            assert rebuilt["markdown"]["available"] is True
            assert rebuilt["markdown"]["data"] == "after update"
            assert rebuilt["chunks"]["total"] == 1
            listed_after_rebuild = _assert_success(
                client.post(
                    "/api/v1/listDir",
                    json={"knCode": kb_code, "directoryPath": "/docs"},
                )
            )["data"][0]
            assert listed_after_rebuild["buildStatus"] == "complete"
            assert listed_after_rebuild["buildCurrentStep"] == "complete"
            status_after_rebuild = _assert_success(
                client.post(
                    "/api/v1/fileBuildStatus",
                    json={"knCode": kb_code, "filePath": file_path},
                )
            )
            assert status_after_rebuild["status"] == "complete"
            assert status_after_rebuild["taskId"] == rebuilt["build"]["taskId"]
    finally:
        if kb_code is not None:
            with TestClient(main_module.app) as cleanup_client:
                _assert_success(
                    cleanup_client.post(
                        "/api/v1/knowledgeBases/delete", json={"knCode": kb_code}
                    )
                )
        asyncio.run(_drop_schema(settings))


def test_running_builds_follow_public_update_delete_move_and_kb_delete_routes(
    monkeypatch, tmp_path
):
    """Resource APIs terminate stale tasks while a moved file keeps its identity."""
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    settings = base_settings.model_copy(
        update={
            "db_schema": f"file_build_mutation_it_{uuid4().hex[:16]}",
            "agent_data_path": tmp_path,
            "embedding_model_name": "file-build-api-integration",
            "embedding_dimension": 3,
            "knowledge_entity_worker_enabled": False,
            "knowledge_build_worker_enabled": True,
            "knowledge_build_worker_id": "file-build-mutation-test",
            "knowledge_build_worker_concurrency": 16,
            "knowledge_build_lease_seconds": 30,
            "knowledge_build_heartbeat_seconds": 1.0,
            "knowledge_build_shutdown_grace_seconds": 2.0,
        }
    )
    publisher = _RecordingPublisher()
    _reset_main_runtime(
        monkeypatch,
        settings=settings,
        publisher=publisher,
        start_worker=False,
    )
    kb_code = None
    kb_deleted = False
    try:
        with TestClient(main_module.app) as client:
            kb_code = _assert_success(
                client.post(
                    "/api/v1/knowledgeBases/create",
                    json={"knName": f"File Build Mutation {uuid4().hex}"},
                )
            )["knCode"]
            for directory in ("/docs", "/docs/subtree", "/archive"):
                _assert_success(
                    client.post(
                        "/api/v1/directories/create",
                        json={"knCode": kb_code, "directoryPath": directory},
                    )
                )
            sources = {
                "/docs/update.txt": b"update source",
                "/docs/delete.txt": b"delete source",
                "/docs/move.txt": b"move source",
                "/docs/subtree/child.txt": b"subtree source",
            }
            for path, content in sources.items():
                name = path.rsplit("/", 1)[-1]
                _assert_success(
                    client.post(
                        "/api/v1/knowledgeItems/import",
                        data={"knCode": kb_code, "filePath": path},
                        files={"fileContent": (name, content, "text/plain")},
                    )
                )

            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": "/docs"},
                )
            )
            assert accepted["acceptedCount"] == 4
            ingestion = main_module._knowledge_item_ingestion_service  # noqa: SLF001
            runner = ingestion.file_build_processing_service.background_runner
            claims = [
                client.portal.call(runner._claim_one)  # noqa: SLF001
                for _ in range(4)
            ]
            assert all(claim is not None for claim in claims)
            by_path = {claim["file_path_snapshot"]: claim for claim in claims}
            assert set(by_path) == set(sources)

            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/update",
                    data={"knCode": kb_code, "filePath": "/docs/update.txt"},
                    files={
                        "fileContent": (
                            "update.txt",
                            b"changed while running",
                            "text/plain",
                        )
                    },
                )
            )
            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/delete",
                    json={"knCode": kb_code, "filePath": "/docs/delete.txt"},
                )
            )
            _assert_success(
                client.post(
                    "/api/v1/directories/delete",
                    json={"knCode": kb_code, "directoryPath": "/docs/subtree"},
                )
            )
            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/move",
                    json={
                        "knCode": kb_code,
                        "sourcePath": ["/docs/move.txt"],
                        "targetFilePath": "/archive/moved.txt",
                    },
                )
            )
            client.portal.call(
                runner._execute_claimed,  # noqa: SLF001
                by_path["/docs/move.txt"],
            )

            batch = _wait_for_batch(
                client, kb_code=kb_code, batch_id=accepted["batchId"]
            )
            assert batch["succeededCount"] == 1
            assert batch["skippedCount"] == 3
            task_by_path = {item["filePathSnapshot"]: item for item in batch["data"]}
            assert task_by_path["/docs/update.txt"]["error"]["errorCode"] == (
                "INPUT_STALE"
            )
            assert task_by_path["/docs/delete.txt"]["error"]["errorCode"] == (
                "SOURCE_DELETED"
            )
            assert (
                task_by_path["/docs/subtree/child.txt"]["error"]["errorCode"]
                == "SOURCE_DELETED"
            )
            assert task_by_path["/docs/move.txt"]["status"] == "SUCCEEDED"

            moved_result = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": "/archive/moved.txt"},
                )
            )
            assert moved_result["isBuilt"] is True
            assert moved_result["fileId"] == str(
                by_path["/docs/move.txt"]["fs_entry_id"]
            )
            assert moved_result["build"]["taskId"] == str(
                by_path["/docs/move.txt"]["kid"]
            )

            build_events = [
                event
                for event in publisher.events
                if event.event_type.startswith("build.")
            ]
            assert (
                len(
                    [
                        event
                        for event in build_events
                        if event.event_type == "build.file.completed"
                    ]
                )
                == 4
            )
            assert (
                len(
                    [
                        event
                        for event in build_events
                        if event.event_type == "build.batch.completed"
                    ]
                )
                == 1
            )

            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/import",
                    data={
                        "knCode": kb_code,
                        "filePath": "/archive/kb-delete.txt",
                    },
                    files={
                        "fileContent": (
                            "kb-delete.txt",
                            b"knowledge base delete",
                            "text/plain",
                        )
                    },
                )
            )
            delete_acceptance = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": "/archive/kb-delete.txt"},
                )
            )
            delete_claim = client.portal.call(runner._claim_one)  # noqa: SLF001
            assert delete_claim is not None
            _assert_success(
                client.post("/api/v1/knowledgeBases/delete", json={"knCode": kb_code})
            )
            kb_deleted = True

            async def _load_deleted_task():
                connection = await build_connection_factory(settings)()
                try:
                    cursor = connection.cursor()
                    await cursor.execute(
                        """
                        SELECT status, error_code
                        FROM knowledge_build_task
                        WHERE kid = %(task_id)s
                        """,
                        {"task_id": int(delete_acceptance["tasks"][0]["taskId"])},
                    )
                    return await cursor.fetchone()
                finally:
                    await connection.close()

            assert client.portal.call(_load_deleted_task) == {
                "status": "skipped",
                "error_code": "KNOWLEDGE_BASE_DELETED",
            }
    finally:
        if kb_code is not None and not kb_deleted:
            with TestClient(main_module.app) as cleanup_client:
                _assert_success(
                    cleanup_client.post(
                        "/api/v1/knowledgeBases/delete", json={"knCode": kb_code}
                    )
                )
        asyncio.run(_drop_schema(settings))
