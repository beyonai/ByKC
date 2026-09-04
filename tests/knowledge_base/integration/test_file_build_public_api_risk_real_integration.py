"""High-risk public API integration coverage for durable File Build state."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_file_build_api_real_integration import (
    _api_settings,
    _assert_success,
    _drop_schema,
    _EchoChunkingService,
    _listed_file,
    _RecordingPublisher,
    _reset_main_runtime,
    _update_file,
    _upload_file,
)

import by_qa.main as main_module
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.services.file_build_background_runner import (
    FileBuildBackgroundRunner,
)
from by_qa.knowledge_common.exceptions import UnsupportedFileTypeError

pytestmark = pytest.mark.integration


def _globbed_file(client: TestClient, *, kb_code: str, path: str) -> dict:
    items = _assert_success(
        client.post(
            "/api/v1/glob",
            json={"knCode": kb_code, "pathRule": path},
        )
    )["data"]
    return next(item for item in items if item["name"] == path)


def _task_page(client: TestClient, *, kb_code: str, **filters) -> dict:
    return _assert_success(
        client.post(
            "/api/v1/knowledgeItems/processingTaskStatus",
            json={"knCode": kb_code, **filters},
        )
    )


def _batch(client: TestClient, *, kb_code: str, batch_id: str) -> dict:
    return _assert_success(
        client.post(
            "/api/v1/knowledgeItems/processingBatchStatus",
            json={
                "knCode": kb_code,
                "batchId": batch_id,
                "includeDetails": True,
            },
        )
    )


def _create_kb(client: TestClient, label: str) -> str:
    return _assert_success(
        client.post(
            "/api/v1/knowledgeBases/create",
            json={"knName": f"{label} {uuid4().hex}"},
        )
    )["knCode"]


def _delete_kb(client: TestClient, kb_code: str) -> None:
    _assert_success(
        client.post("/api/v1/knowledgeBases/delete", json={"knCode": kb_code})
    )


def _prevent_automatic_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _do_not_start(self):  # pylint: disable=unused-argument
        return None

    monkeypatch.setattr(FileBuildBackgroundRunner, "start", _do_not_start)


def test_pending_running_and_completed_are_consistent_across_public_status_apis(
    monkeypatch, tmp_path
):
    """One real task must expose one coherent state through every public read API."""
    settings = _api_settings(
        tmp_path,
        schema_prefix="file_build_public_states_it",
        worker_id="file-build-public-states-test",
    )
    publisher = _RecordingPublisher()
    _prevent_automatic_claims(monkeypatch)
    _reset_main_runtime(
        monkeypatch, settings=settings, publisher=publisher, start_worker=False
    )
    kb_codes: list[str] = []
    try:
        with TestClient(main_module.app) as client:
            runner = main_module._knowledge_item_ingestion_service.file_build_processing_service.background_runner  # noqa: SLF001
            client.portal.call(runner.stop)
            kb_code = _create_kb(client, "Public Build States")
            other_kb_code = _create_kb(client, "Other Public Build States")
            kb_codes.extend((kb_code, other_kb_code))
            path = "/states/file.txt"
            _upload_file(client, kb_code=kb_code, path=path, content=b"state data")

            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            task_id = accepted["tasks"][0]["taskId"]
            file_id = accepted["tasks"][0]["fileId"]
            batch_id = accepted["batchId"]

            pending_list = _listed_file(client, kb_code=kb_code, path=path)
            pending_glob = _globbed_file(client, kb_code=kb_code, path=path)
            pending_status = _assert_success(
                client.post(
                    "/api/v1/fileBuildStatus",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            pending_result = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            pending_tasks = _task_page(
                client,
                kb_code=kb_code,
                taskId=task_id,
                taskType="FILE_BUILD",
                includeDetails=True,
            )
            pending_batch = _batch(client, kb_code=kb_code, batch_id=batch_id)
            # Legacy browse/status APIs intentionally collapse PENDING into running.
            assert (
                pending_list["buildStatus"] == pending_glob["buildStatus"] == "running"
            )
            assert pending_status["status"] == "running"
            assert pending_result["isBuilt"] is False
            assert pending_tasks["data"][0]["status"] == "PENDING"
            assert pending_batch["status"] == "PENDING"
            assert pending_batch["pendingCount"] == 1

            # A task ID is meaningful only inside its task pool and knowledge base.
            assert (
                _task_page(
                    client,
                    kb_code=other_kb_code,
                    taskId=task_id,
                    taskType="FILE_BUILD",
                )["total"]
                == 0
            )
            mismatch = client.post(
                "/api/v1/knowledgeItems/processingTaskStatus",
                json={
                    "knCode": kb_code,
                    "fileId": file_id,
                    "filePath": "/does/not/exist.txt",
                    "taskType": "FILE_BUILD",
                },
            ).json()
            assert mismatch["resultCode"] == "-1"

            claim = client.portal.call(runner._claim_one)  # noqa: SLF001
            assert claim is not None
            running_list = _listed_file(client, kb_code=kb_code, path=path)
            running_glob = _globbed_file(client, kb_code=kb_code, path=path)
            running_status = _assert_success(
                client.post(
                    "/api/v1/fileBuildStatus",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            running_tasks = _task_page(
                client, kb_code=kb_code, fileId=file_id, taskType="FILE_BUILD"
            )
            running_batch = _batch(client, kb_code=kb_code, batch_id=batch_id)
            assert (
                running_list["buildStatus"] == running_glob["buildStatus"] == "running"
            )
            assert running_status["status"] == "running"
            assert running_tasks["data"][0]["status"] == "RUNNING"
            assert running_batch["status"] == "PROCESSING"
            assert running_batch["runningCount"] == 1

            client.portal.call(runner._execute_claimed, claim)  # noqa: SLF001
            completed_list = _listed_file(client, kb_code=kb_code, path=path)
            completed_glob = _globbed_file(client, kb_code=kb_code, path=path)
            completed_status = _assert_success(
                client.post(
                    "/api/v1/fileBuildStatus",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            completed_result = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            completed_batch = _batch(client, kb_code=kb_code, batch_id=batch_id)
            assert (
                completed_list["buildStatus"]
                == completed_glob["buildStatus"]
                == "complete"
            )
            assert completed_status["status"] == "complete"
            assert completed_result["isBuilt"] is True
            assert completed_batch["status"] == "COMPLETED"
            assert completed_batch["succeededCount"] == 1
    finally:
        if kb_codes:
            with TestClient(main_module.app) as cleanup_client:
                for kb_code in kb_codes:
                    _delete_kb(cleanup_client, kb_code)
        asyncio.run(_drop_schema(settings))


@pytest.mark.parametrize(
    "claim_before_update", [False, True], ids=["pending", "running"]
)
def test_active_checksum_aba_never_resurrects_the_invalidated_task(
    monkeypatch, tmp_path, claim_before_update
):
    """A -> B -> A must not make an invalidated active task current again."""
    settings = _api_settings(
        tmp_path,
        schema_prefix="file_build_active_aba_it",
        worker_id="file-build-active-aba-test",
    )
    publisher = _RecordingPublisher()
    _prevent_automatic_claims(monkeypatch)
    _reset_main_runtime(
        monkeypatch, settings=settings, publisher=publisher, start_worker=False
    )
    kb_code = None
    try:
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, "Active Checksum ABA")
            path = "/aba/active.txt"
            _upload_file(client, kb_code=kb_code, path=path, content=b"content-a")
            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            if claim_before_update:
                runner = main_module._knowledge_item_ingestion_service.file_build_processing_service.background_runner  # noqa: SLF001
                assert client.portal.call(runner._claim_one) is not None  # noqa: SLF001

            _update_file(client, kb_code=kb_code, path=path, content=b"content-b")
            _update_file(client, kb_code=kb_code, path=path, content=b"content-a")

            listed = _listed_file(client, kb_code=kb_code, path=path)
            globbed = _globbed_file(client, kb_code=kb_code, path=path)
            status = client.post(
                "/api/v1/fileBuildStatus",
                json={"knCode": kb_code, "filePath": path},
            ).json()
            result = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            history = _task_page(
                client,
                kb_code=kb_code,
                fileId=accepted["tasks"][0]["fileId"],
                taskType="FILE_BUILD",
                latestOnly=False,
                includeDetails=True,
            )
            assert listed["buildStatus"] is None
            assert globbed["buildStatus"] is None
            assert status["resultCode"] == "-1"
            assert result["isBuilt"] is False
            assert history["data"][0]["status"] == "SKIPPED"
            assert history["data"][0]["error"]["errorCode"] == "INPUT_STALE"

            rebuilt = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            assert rebuilt["acceptedCount"] == 1
            assert rebuilt["reusedCount"] == 0
            assert rebuilt["tasks"][0]["taskId"] != accepted["tasks"][0]["taskId"]
    finally:
        if kb_code is not None:
            with TestClient(main_module.app) as cleanup_client:
                _delete_kb(cleanup_client, kb_code)
        asyncio.run(_drop_schema(settings))


def test_pending_directory_build_follows_subtree_move_by_file_id(monkeypatch, tmp_path):
    """Moving a directory must not strand its accepted descendant Build tasks."""
    settings = _api_settings(
        tmp_path,
        schema_prefix="file_build_directory_move_it",
        worker_id="file-build-directory-move-test",
    )
    publisher = _RecordingPublisher()
    _prevent_automatic_claims(monkeypatch)
    _reset_main_runtime(
        monkeypatch, settings=settings, publisher=publisher, start_worker=False
    )
    kb_code = None
    try:
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, "Directory Build Move")
            paths = {
                "/source/a.txt": b"alpha",
                "/source/nested/b.txt": b"beta",
            }
            for path, content in paths.items():
                _upload_file(client, kb_code=kb_code, path=path, content=content)
            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": "/source"},
                )
            )
            original_by_id = {
                item["fileId"]: item["filePathSnapshot"] for item in accepted["tasks"]
            }
            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/move",
                    json={
                        "knCode": kb_code,
                        "sourcePath": ["/source"],
                        "targetDirectoryPath": "/archive",
                    },
                )
            )

            runner = main_module._knowledge_item_ingestion_service.file_build_processing_service.background_runner  # noqa: SLF001
            claims = [client.portal.call(runner._claim_one) for _ in paths]  # noqa: SLF001
            assert all(claim is not None for claim in claims)
            for claim in claims:
                client.portal.call(runner._execute_claimed, claim)  # noqa: SLF001

            batch = _batch(client, kb_code=kb_code, batch_id=accepted["batchId"])
            assert batch["status"] == "COMPLETED"
            assert batch["succeededCount"] == 2
            assert {
                item["fileId"]: item["filePathSnapshot"] for item in batch["data"]
            } == original_by_id
            for old_path in paths:
                new_path = f"/archive{old_path}"
                listed = _listed_file(client, kb_code=kb_code, path=new_path)
                globbed = _globbed_file(client, kb_code=kb_code, path=new_path)
                result = _assert_success(
                    client.post(
                        "/api/v1/buildResult",
                        json={"knCode": kb_code, "filePath": new_path},
                    )
                )
                assert listed["buildStatus"] == globbed["buildStatus"] == "complete"
                assert result["isBuilt"] is True
                assert result["fileId"] in original_by_id
    finally:
        if kb_code is not None:
            with TestClient(main_module.app) as cleanup_client:
                _delete_kb(cleanup_client, kb_code)
        asyncio.run(_drop_schema(settings))


def test_deleted_pending_file_remains_queryable_by_stable_file_id(
    monkeypatch, tmp_path
):
    """Deletion removes path lookup but must retain the file-id-addressable audit row."""
    settings = _api_settings(
        tmp_path,
        schema_prefix="file_build_deleted_history_it",
        worker_id="file-build-deleted-history-test",
    )
    publisher = _RecordingPublisher()
    _prevent_automatic_claims(monkeypatch)
    _reset_main_runtime(
        monkeypatch, settings=settings, publisher=publisher, start_worker=False
    )
    kb_code = None
    try:
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, "Deleted Build History")
            path = "/deleted/pending.txt"
            _upload_file(client, kb_code=kb_code, path=path, content=b"delete me")
            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            file_id = accepted["tasks"][0]["fileId"]
            _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/delete",
                    json={"knCode": kb_code, "filePath": path},
                )
            )

            history = _task_page(
                client,
                kb_code=kb_code,
                fileId=file_id,
                taskType="FILE_BUILD",
                latestOnly=False,
                includeDetails=True,
            )
            assert history["total"] == 1
            assert history["data"][0]["status"] == "SKIPPED"
            assert history["data"][0]["error"]["errorCode"] == "SOURCE_DELETED"
            batch = _batch(client, kb_code=kb_code, batch_id=accepted["batchId"])
            assert batch["status"] == "COMPLETED"
            assert batch["skippedCount"] == 1

            by_old_path = client.post(
                "/api/v1/knowledgeItems/processingTaskStatus",
                json={
                    "knCode": kb_code,
                    "filePath": path,
                    "taskType": "FILE_BUILD",
                    "latestOnly": False,
                },
            ).json()
            assert by_old_path["resultCode"] == "-1"
            assert "file not found" in by_old_path["resultMsg"]
    finally:
        if kb_code is not None:
            with TestClient(main_module.app) as cleanup_client:
                _delete_kb(cleanup_client, kb_code)
        asyncio.run(_drop_schema(settings))


def test_unsupported_checksum_aba_does_not_reuse_pre_update_task(monkeypatch, tmp_path):
    """An UNSUPPORTED A task is historical after A -> B -> A content changes."""

    class _UnsupportedChunkingService:
        def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
            del file_bytes
            raise UnsupportedFileTypeError(f"unsupported file type: {file_type}")

    settings = _api_settings(
        tmp_path,
        schema_prefix="file_build_unsupported_aba_it",
        worker_id="file-build-unsupported-aba-test",
    )
    publisher = _RecordingPublisher()
    _prevent_automatic_claims(monkeypatch)
    _reset_main_runtime(
        monkeypatch, settings=settings, publisher=publisher, start_worker=False
    )
    kb_code = None
    try:
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, "Unsupported Checksum ABA")
            path = "/unsupported/file.png"
            _upload_file(client, kb_code=kb_code, path=path, content=b"png-a")
            ingestion = main_module._knowledge_item_ingestion_service  # noqa: SLF001
            runner = ingestion.file_build_processing_service.background_runner
            runner.execution_service.document_chunking_service = (
                _UnsupportedChunkingService()
            )
            first = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            claim = client.portal.call(runner._claim_one)  # noqa: SLF001
            assert claim is not None
            client.portal.call(runner._execute_claimed, claim)  # noqa: SLF001
            first_batch = _batch(client, kb_code=kb_code, batch_id=first["batchId"])
            assert first_batch["unsupportedCount"] == 1

            _update_file(client, kb_code=kb_code, path=path, content=b"png-b")
            _update_file(client, kb_code=kb_code, path=path, content=b"png-a")
            listed = _listed_file(client, kb_code=kb_code, path=path)
            globbed = _globbed_file(client, kb_code=kb_code, path=path)
            assert listed["buildStatus"] is None
            assert globbed["buildStatus"] is None

            second = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            assert second["acceptedCount"] == 1
            assert second["reusedCount"] == 0
            assert second["tasks"][0]["taskId"] != first["tasks"][0]["taskId"]
    finally:
        if kb_code is not None:
            with TestClient(main_module.app) as cleanup_client:
                _delete_kb(cleanup_client, kb_code)
        asyncio.run(_drop_schema(settings))


def test_one_directory_batch_aggregates_every_terminal_outcome_once(
    monkeypatch, tmp_path
):
    """A mixed terminal batch must complete once with exact counters and callbacks."""

    class _MixedChunkingService(_EchoChunkingService):
        def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
            if file_bytes == b"unsupported":
                raise UnsupportedFileTypeError("unsupported by integration test")
            if file_bytes == b"failed":
                raise RuntimeError("failed by integration test")
            return super().extract_text_from_file(file_bytes, file_type)

    settings = _api_settings(
        tmp_path,
        schema_prefix="file_build_mixed_terminal_it",
        worker_id="file-build-mixed-terminal-test",
    )
    publisher = _RecordingPublisher()
    _prevent_automatic_claims(monkeypatch)
    _reset_main_runtime(
        monkeypatch, settings=settings, publisher=publisher, start_worker=False
    )
    kb_code = None
    try:
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, "Mixed Terminal Build")
            sources = {
                "/mixed/success.txt": b"success",
                "/mixed/unsupported.txt": b"unsupported",
                "/mixed/failed.txt": b"failed",
                "/mixed/stale.txt": b"stale-a",
            }
            for path, content in sources.items():
                _upload_file(client, kb_code=kb_code, path=path, content=content)
            ingestion = main_module._knowledge_item_ingestion_service  # noqa: SLF001
            runner = ingestion.file_build_processing_service.background_runner
            runner.execution_service.document_chunking_service = _MixedChunkingService(
                dimension=settings.embedding_dimension
            )
            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": "/mixed"},
                )
            )
            claims = [client.portal.call(runner._claim_one) for _ in sources]  # noqa: SLF001
            assert all(claim is not None for claim in claims)
            claim_by_path = {claim["file_path_snapshot"]: claim for claim in claims}

            _update_file(
                client,
                kb_code=kb_code,
                path="/mixed/stale.txt",
                content=b"stale-b",
            )
            for path in (
                "/mixed/success.txt",
                "/mixed/unsupported.txt",
                "/mixed/failed.txt",
            ):
                client.portal.call(  # noqa: SLF001
                    runner._execute_claimed, claim_by_path[path]
                )

            batch = _batch(client, kb_code=kb_code, batch_id=accepted["batchId"])
            assert batch["status"] == "COMPLETED"
            assert batch["completedCount"] == 4
            assert batch["succeededCount"] == 1
            assert batch["failedCount"] == 1, batch
            assert batch["skippedCount"] == 1
            assert batch["unsupportedCount"] == 1
            assert {item["status"] for item in batch["data"]} == {
                "SUCCEEDED",
                "FAILED",
                "SKIPPED",
                "UNSUPPORTED",
            }
            file_events = [
                event
                for event in publisher.events
                if event.event_type == "build.file.completed"
            ]
            batch_events = [
                event
                for event in publisher.events
                if event.event_type == "build.batch.completed"
                and event.payload.batch_id == accepted["batchId"]
            ]
            assert len(file_events) == 4
            assert len({event.payload.task_id for event in file_events}) == 4
            assert len(batch_events) == 1
    finally:
        if kb_code is not None:
            with TestClient(main_module.app) as cleanup_client:
                _delete_kb(cleanup_client, kb_code)
        asyncio.run(_drop_schema(settings))


@pytest.mark.parametrize(
    "missing_artifact",
    ["markdown_locator", "chunks", "embedding", "retrieval"],
)
def test_incomplete_succeeded_artifacts_are_not_reported_as_current_complete(
    monkeypatch, tmp_path, missing_artifact
):
    """Artifact loss must not leave browse/status claiming a usable complete Build."""
    settings = _api_settings(
        tmp_path,
        schema_prefix="file_build_artifact_audit_it",
        worker_id="file-build-artifact-audit-test",
    )
    publisher = _RecordingPublisher()
    _prevent_automatic_claims(monkeypatch)
    _reset_main_runtime(
        monkeypatch, settings=settings, publisher=publisher, start_worker=False
    )
    kb_code = None
    try:
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, "Artifact Audit Build")
            path = "/audit/file.txt"
            _upload_file(client, kb_code=kb_code, path=path, content=b"audit")
            accepted = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            ingestion = main_module._knowledge_item_ingestion_service  # noqa: SLF001
            runner = ingestion.file_build_processing_service.background_runner
            claim = client.portal.call(runner._claim_one)  # noqa: SLF001
            assert claim is not None
            client.portal.call(runner._execute_claimed, claim)  # noqa: SLF001

            async def _remove_artifact():
                connection = await build_connection_factory(settings)()
                try:
                    cursor = connection.cursor()
                    table = (
                        ingestion.knowledge_item_chunk_repository.embedding_table_name
                    )
                    file_id = int(accepted["tasks"][0]["fileId"])
                    if missing_artifact == "markdown_locator":
                        await cursor.execute(
                            """
                            UPDATE knowledge_fs_entry
                            SET markdown_bucket_name = NULL,
                                markdown_object_key = NULL
                            WHERE kid = %(file_id)s
                            """,
                            {"file_id": file_id},
                        )
                    elif missing_artifact == "chunks":
                        await cursor.execute(
                            "DELETE FROM knowledge_chunk WHERE fs_entry_id = %(file_id)s",
                            {"file_id": file_id},
                        )
                    else:
                        target = (
                            table
                            if missing_artifact == "embedding"
                            else "knowledge_chunk_retrieval_mv"
                        )
                        await cursor.execute(
                            f"""
                            DELETE FROM {target}
                            WHERE chunk_id IN (
                                SELECT kid FROM knowledge_chunk
                                WHERE fs_entry_id = %(file_id)s
                            )
                            """,
                            {"file_id": file_id},
                        )
                    await connection.commit()
                finally:
                    await connection.close()

            client.portal.call(_remove_artifact)
            result = _assert_success(
                client.post(
                    "/api/v1/buildResult",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            listed = _listed_file(client, kb_code=kb_code, path=path)
            globbed = _globbed_file(client, kb_code=kb_code, path=path)
            status = client.post(
                "/api/v1/fileBuildStatus",
                json={"knCode": kb_code, "filePath": path},
            ).json()
            assert result["isBuilt"] is False
            assert listed["buildStatus"] is None
            assert globbed["buildStatus"] is None
            assert status["resultCode"] == "-1"

            rebuild = _assert_success(
                client.post(
                    "/api/v1/fileToMarkdownIndex",
                    json={"knCode": kb_code, "filePath": path},
                )
            )
            assert rebuild["acceptedCount"] == 1
            assert rebuild["reusedCount"] == 0
    finally:
        if kb_code is not None:
            with TestClient(main_module.app) as cleanup_client:
                _delete_kb(cleanup_client, kb_code)
        asyncio.run(_drop_schema(settings))
