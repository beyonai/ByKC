"""Real OpenGauss/MinIO tests for File Build concurrency and failure fencing."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from psycopg import sql

from by_qa.config import get_settings
from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest
from by_qa.knowledge_base.events import KnowledgeEventPublisherInvoker
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.storage_s3 import build_s3_storage_provider
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_build_acceptance_repository import (
    KnowledgeBuildAcceptanceRepository,
)
from by_qa.knowledge_base.repositories.knowledge_build_batch_repository import (
    KnowledgeBuildBatchRepository,
)
from by_qa.knowledge_base.repositories.knowledge_build_task_repository import (
    KnowledgeBuildTaskRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fetch_cache_repository import (
    KnowledgeFetchCacheRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_chunk_repository import (
    KnowledgeItemChunkRepository,
)
from by_qa.knowledge_base.repositories.retrieval_projection_repository import (
    RetrievalProjectionRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.file_build_background_runner import (
    FileBuildBackgroundRunner,
)
from by_qa.knowledge_base.services.file_build_execution_service import (
    FileBuildExecutionService,
)
from by_qa.knowledge_base.services.file_build_models import (
    EmbeddingBuildProfile,
    FileBuildProfile,
)
from by_qa.knowledge_base.services.file_build_processing_service import (
    FileBuildProcessingService,
)
from by_qa.knowledge_base.services.file_build_terminal_event_service import (
    FileBuildTerminalEventService,
)
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:
        self.events.append(event)


class _GatedChunkingService:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        assert file_type == "txt"
        self.started.set()
        if not self.release.wait(timeout=15):
            raise TimeoutError("test extraction gate was not released")
        return file_bytes.decode()

    def chunk_and_embed(
        self, markdown_bytes: bytes, *, filename: str
    ) -> list[KnowledgeItemChunkPayload]:
        assert filename.endswith(".md")
        text = markdown_bytes.decode()
        return [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=max(1, len(text.splitlines())),
                chunk_text=text,
                embedding=[0.1, 0.2, 0.3],
            )
        ]


class _ImmediateChunkingService(_GatedChunkingService):
    def __init__(self) -> None:
        super().__init__()
        self.release.set()


class _MarkdownWriteGate:
    def __init__(self, delegate, markdown_namespace: str) -> None:
        self.delegate = delegate
        self.markdown_namespace = markdown_namespace
        self.written = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def write(self, location, data, *, content_type=None):
        result = await self.delegate.write(location, data, content_type=content_type)
        if location.namespace == self.markdown_namespace:
            self.written.set()
            await self.release.wait()
        return result


@dataclass
class _Harness:
    settings: object
    connection_factory: object
    storage: object
    task_repository: KnowledgeBuildTaskRepository
    batch_repository: KnowledgeBuildBatchRepository
    fs_repository: KnowledgeFsEntryRepository
    processing_service: FileBuildProcessingService
    execution_service: FileBuildExecutionService
    publisher: _RecordingPublisher
    knowledge_base_id: int
    file_ids: dict[str, int]
    locations: list[object] = field(default_factory=list)


async def _make_harness(*, chunking_service=None, storage_wrapper=None) -> _Harness:
    base = get_settings()
    if not base.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    model = "file-build-concurrency-fault"
    settings = base.model_copy(
        update={
            "db_schema": f"file_build_fault_it_{uuid4().hex[:16]}",
            "embedding_model_name": model,
            "embedding_dimension": 3,
        }
    )
    connection_factory = build_connection_factory(settings)
    bootstrap = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name=model, embedding_dimension=3
    )
    connection = await connection_factory()
    try:
        await bootstrap.apply(connection)
    finally:
        await connection.close()

    base_storage = build_s3_storage_provider(settings)
    await base_storage.ensure_ready()
    storage = storage_wrapper(base_storage) if storage_wrapper else base_storage
    task_repository = KnowledgeBuildTaskRepository()
    batch_repository = KnowledgeBuildBatchRepository()
    fs_repository = KnowledgeFsEntryRepository()
    publisher = _RecordingPublisher()
    terminal = FileBuildTerminalEventService(
        connection_factory=connection_factory,
        batch_repository=batch_repository,
        event_publisher_invoker=KnowledgeEventPublisherInvoker(publisher=publisher),
    )
    processing = FileBuildProcessingService(
        connection_factory=connection_factory,
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=fs_repository,
        acceptance_repository=KnowledgeBuildAcceptanceRepository(
            bootstrap.embedding_table_name
        ),
        batch_repository=batch_repository,
        task_repository=task_repository,
        build_profile=FileBuildProfile(
            embedding=EmbeddingBuildProfile(model=model, dimension=3)
        ),
        terminal_event_service=terminal,
    )
    execution = FileBuildExecutionService(
        connection_factory=connection_factory,
        task_repository=task_repository,
        batch_repository=batch_repository,
        fs_entry_repository=fs_repository,
        chunk_repository=KnowledgeItemChunkRepository(bootstrap.embedding_table_name),
        retrieval_repository=RetrievalProjectionRepository(),
        fetch_cache_repository=KnowledgeFetchCacheRepository(),
        storage_provider=storage,
        document_chunking_service=chunking_service or _ImmediateChunkingService(),
        embedding_dimension=3,
        terminal_event_service=terminal,
    )

    locations = []
    connection = await connection_factory()
    try:
        cursor = connection.cursor()
        await cursor.execute(
            "INSERT INTO knowledge_base (kb_name) VALUES (%(name)s) RETURNING kid",
            {"name": f"file-build-fault-{uuid4().hex}"},
        )
        kb_id = int((await cursor.fetchone())["kid"])
        await cursor.execute(
            """
            INSERT INTO knowledge_fs_entry (
                knowledge_base_id, entry_type, is_root, name, path_ltree,
                depth, virtual_path
            ) VALUES (%(kb)s, 'DIRECTORY', FALSE, 'docs', 'd1_docs', 1, '/docs')
            RETURNING kid
            """,
            {"kb": kb_id},
        )
        docs_id = int((await cursor.fetchone())["kid"])
        await cursor.execute(
            """
            INSERT INTO knowledge_fs_entry (
                knowledge_base_id, parent_entry_id, entry_type, is_root, name,
                path_ltree, depth, virtual_path
            ) VALUES (
                %(kb)s, %(parent)s, 'DIRECTORY', FALSE, 'nested',
                'd1_docs.d2_nested', 2, '/docs/nested'
            ) RETURNING kid
            """,
            {"kb": kb_id, "parent": docs_id},
        )
        directories = {
            "/docs": docs_id,
            "/docs/nested": int((await cursor.fetchone())["kid"]),
        }
        file_ids = {}
        for index, (path, parent_path) in enumerate(
            (("/docs/a.txt", "/docs"), ("/docs/nested/b.txt", "/docs/nested")),
            start=1,
        ):
            content = f"content-{index}".encode()
            checksum = hashlib.sha256(content).hexdigest()
            name = path.rsplit("/", 1)[-1]
            path_ltree = "d1_docs.f2_a" if index == 1 else "d1_docs.d2_nested.f3_b"
            await cursor.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, parent_entry_id, entry_type, is_root,
                    name, path_ltree, depth, virtual_path, mime_type, checksum,
                    file_size
                ) VALUES (
                    %(kb)s, %(parent)s, 'FILE', FALSE, %(name)s,
                    %(path_ltree)s::ltree, %(depth)s, %(path)s, 'text/plain',
                    %(checksum)s, %(size)s
                ) RETURNING kid
                """,
                {
                    "kb": kb_id,
                    "parent": directories[parent_path],
                    "name": name,
                    "path_ltree": path_ltree,
                    "depth": 2 if index == 1 else 3,
                    "path": path,
                    "checksum": checksum,
                    "size": len(content),
                },
            )
            file_id = int((await cursor.fetchone())["kid"])
            file_ids[path] = file_id
            location = base_storage.build_original_location(
                kb_code=str(kb_id),
                knowledge_base_id=kb_id,
                fs_entry_id=file_id,
                file_path=path,
                mime_type="text/plain",
            )
            await base_storage.write(location, content, content_type="text/plain")
            locations.append(location)
            await cursor.execute(
                """
                UPDATE knowledge_fs_entry
                SET file_bucket_name=%(bucket)s, file_object_key=%(key)s
                WHERE kid=%(kid)s
                """,
                {
                    "bucket": location.namespace,
                    "key": location.key,
                    "kid": file_id,
                },
            )
        await connection.commit()
    finally:
        await connection.close()

    return _Harness(
        settings=settings,
        connection_factory=connection_factory,
        storage=storage,
        task_repository=task_repository,
        batch_repository=batch_repository,
        fs_repository=fs_repository,
        processing_service=processing,
        execution_service=execution,
        publisher=publisher,
        knowledge_base_id=kb_id,
        file_ids=file_ids,
        locations=locations,
    )


async def _cleanup(harness: _Harness) -> None:
    base_storage = getattr(harness.storage, "delegate", harness.storage)
    for location in harness.locations:
        await base_storage.delete_quietly(location)
    for path, file_id in harness.file_ids.items():
        markdown = base_storage.build_markdown_location(
            kb_code=str(harness.knowledge_base_id),
            knowledge_base_id=harness.knowledge_base_id,
            fs_entry_id=file_id,
            file_path=path,
        )
        await base_storage.delete_quietly(markdown)
    connection = await harness.connection_factory()
    try:
        await connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(
                sql.Identifier(harness.settings.db_schema)
            )
        )
        await connection.commit()
    finally:
        await connection.close()


async def _wait_thread_event(event: threading.Event) -> None:
    for _ in range(300):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    pytest.fail("thread gate did not start")


async def _task_row(harness: _Harness, task_id: int):
    connection = await harness.connection_factory()
    try:
        cursor = connection.cursor()
        await cursor.execute(
            "SELECT * FROM knowledge_build_task WHERE kid=%(kid)s", {"kid": task_id}
        )
        return await cursor.fetchone()
    finally:
        await connection.close()


async def test_same_file_and_overlapping_directory_acceptance_serialize() -> None:
    harness = await _make_harness()
    try:
        same = await asyncio.gather(
            *[
                harness.processing_service.accept(
                    FileToMarkdownIndexRequest(
                        knCode=str(harness.knowledge_base_id), filePath="/docs/a.txt"
                    )
                )
                for _ in range(2)
            ]
        )
        assert sum(item["acceptedCount"] for item in same) == 1
        assert sum(item["reusedCount"] for item in same) == 1

        overlap = await asyncio.gather(
            harness.processing_service.accept(
                FileToMarkdownIndexRequest(
                    knCode=str(harness.knowledge_base_id), filePath="/docs"
                )
            ),
            harness.processing_service.accept(
                FileToMarkdownIndexRequest(
                    knCode=str(harness.knowledge_base_id), filePath="/docs/nested"
                )
            ),
        )
        # a.txt was already active; only b.txt may be newly accepted.
        assert sum(item["acceptedCount"] for item in overlap) == 1
        assert sum(item["reusedCount"] for item in overlap) == 2
        connection = await harness.connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                SELECT COUNT(*) AS count FROM knowledge_build_task
                WHERE status IN ('pending', 'running')
                """
            )
            assert int((await cursor.fetchone())["count"]) == 2
        finally:
            await connection.close()
    finally:
        await _cleanup(harness)


async def test_heartbeat_keeps_long_extraction_away_from_reaper() -> None:
    chunking = _GatedChunkingService()
    harness = await _make_harness(chunking_service=chunking)
    runner = FileBuildBackgroundRunner(
        connection_factory=harness.connection_factory,
        task_repository=harness.task_repository,
        batch_repository=harness.batch_repository,
        execution_service=harness.execution_service,
        worker_id="heartbeat-owner",
        concurrency=1,
        lease_seconds=2,
        heartbeat_seconds=0.2,
    )
    try:
        accepted = await harness.processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(harness.knowledge_base_id), filePath="/docs/a.txt"
            )
        )
        assert await runner.run_claim_cycle() == 1
        await _wait_thread_event(chunking.started)
        await asyncio.sleep(2.3)
        assert await runner.run_reaper_cycle() == 0
        active = list(runner._active_tasks)  # noqa: SLF001
        chunking.release.set()
        await asyncio.gather(*active)
        row = await _task_row(harness, int(accepted["tasks"][0]["taskId"]))
        assert row["status"] == "succeeded"
        assert [event.event_type for event in harness.publisher.events] == [
            "build.file.completed",
            "build.batch.completed",
        ]
    finally:
        chunking.release.set()
        await _cleanup(harness)


async def test_task_timeout_fences_late_extraction_and_completes_batch() -> None:
    chunking = _GatedChunkingService()
    harness = await _make_harness(chunking_service=chunking)
    runner = FileBuildBackgroundRunner(
        connection_factory=harness.connection_factory,
        task_repository=harness.task_repository,
        batch_repository=harness.batch_repository,
        execution_service=harness.execution_service,
        worker_id="timeout-owner",
        concurrency=1,
        task_timeout_seconds=0.2,
        lease_seconds=3,
        heartbeat_seconds=0.5,
    )
    try:
        accepted = await harness.processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(harness.knowledge_base_id), filePath="/docs/a.txt"
            )
        )
        assert await runner.run_claim_cycle() == 1
        await _wait_thread_event(chunking.started)
        active = list(runner._active_tasks)  # noqa: SLF001
        await asyncio.gather(*active)
        chunking.release.set()
        row = await _task_row(harness, int(accepted["tasks"][0]["taskId"]))
        assert (row["status"], row["error_code"], row["outcome_uncertain"]) == (
            "failed",
            "TASK_TIMEOUT",
            True,
        )
        assert [event.event_type for event in harness.publisher.events] == [
            "build.file.completed",
            "build.batch.completed",
        ]
    finally:
        chunking.release.set()
        await _cleanup(harness)


async def test_shutdown_leaves_work_fenced_for_a_new_reaper() -> None:
    chunking = _GatedChunkingService()
    harness = await _make_harness(chunking_service=chunking)
    runner = FileBuildBackgroundRunner(
        connection_factory=harness.connection_factory,
        task_repository=harness.task_repository,
        batch_repository=harness.batch_repository,
        execution_service=harness.execution_service,
        worker_id="stopping-owner",
        concurrency=1,
        lease_seconds=2,
        heartbeat_seconds=0.2,
        shutdown_grace_seconds=0.1,
    )
    new_runner = FileBuildBackgroundRunner(
        connection_factory=harness.connection_factory,
        task_repository=harness.task_repository,
        batch_repository=harness.batch_repository,
        execution_service=harness.execution_service,
        worker_id="replacement-owner",
        concurrency=1,
        lease_seconds=2,
        heartbeat_seconds=0.2,
    )
    try:
        accepted = await harness.processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(harness.knowledge_base_id), filePath="/docs/a.txt"
            )
        )
        assert await runner.run_claim_cycle() == 1
        await _wait_thread_event(chunking.started)
        await runner.stop()
        await asyncio.sleep(2.1)
        assert await new_runner.run_reaper_cycle() == 1
        row = await _task_row(harness, int(accepted["tasks"][0]["taskId"]))
        assert (row["status"], row["error_code"]) == ("failed", "WORKER_LOST")
        assert [event.event_type for event in harness.publisher.events] == [
            "build.file.completed",
            "build.batch.completed",
        ]
    finally:
        chunking.release.set()
        await _cleanup(harness)


async def test_lease_expiry_after_markdown_write_does_not_leave_orphan_object() -> None:
    gate_holder = {}

    def wrap(storage):
        gate = _MarkdownWriteGate(storage, storage.storage.markdown_bucket_name)
        gate_holder["gate"] = gate
        return gate

    harness = await _make_harness(storage_wrapper=wrap)
    gate = gate_holder["gate"]
    runner = FileBuildBackgroundRunner(
        connection_factory=harness.connection_factory,
        task_repository=harness.task_repository,
        batch_repository=harness.batch_repository,
        execution_service=harness.execution_service,
        worker_id="commit-boundary-owner",
        concurrency=1,
        lease_seconds=1,
        heartbeat_seconds=0.2,
    )
    try:
        accepted = await harness.processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(harness.knowledge_base_id), filePath="/docs/a.txt"
            )
        )
        assert await runner.run_claim_cycle() == 1
        await asyncio.wait_for(gate.written.wait(), timeout=5)
        await asyncio.sleep(1.1)
        reaper = asyncio.create_task(runner.run_reaper_cycle())
        active = list(runner._active_tasks)  # noqa: SLF001
        gate.release.set()
        await asyncio.gather(*active)
        reaped = await asyncio.wait_for(reaper, timeout=5)
        if reaped == 0:
            reaped += await runner.run_reaper_cycle()
        assert reaped == 1
        row = await _task_row(harness, int(accepted["tasks"][0]["taskId"]))
        assert (row["status"], row["error_code"]) == ("failed", "WORKER_LOST")
        markdown = gate.delegate.build_markdown_location(
            kb_code=str(harness.knowledge_base_id),
            knowledge_base_id=harness.knowledge_base_id,
            fs_entry_id=harness.file_ids["/docs/a.txt"],
            file_path="/docs/a.txt",
        )
        with pytest.raises(Exception):
            await gate.delegate.read(markdown)
    finally:
        gate.release.set()
        await _cleanup(harness)


async def test_worker_and_update_lock_order_has_no_deadlock() -> None:
    harness = await _make_harness()
    worker = await harness.connection_factory()
    updater = await harness.connection_factory()
    try:
        accepted = await harness.processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(harness.knowledge_base_id), filePath="/docs/a.txt"
            )
        )
        task_id = int(accepted["tasks"][0]["taskId"])
        claim_connection = await harness.connection_factory()
        try:
            claim_cursor = claim_connection.cursor()
            claim = await harness.task_repository.claim_next_task(
                claim_cursor,
                worker_id="deadlock-owner",
                lease_token=uuid4().hex,
                lease_seconds=30,
            )
            await claim_connection.commit()
            assert claim is not None
        finally:
            await claim_connection.close()

        worker_cursor = worker.cursor()
        updater_cursor = updater.cursor()
        await updater_cursor.execute(
            "SELECT kid FROM knowledge_fs_entry WHERE kid=%(kid)s FOR UPDATE",
            {"kid": harness.file_ids["/docs/a.txt"]},
        )

        worker_lock = asyncio.create_task(
            harness.execution_service._lock_input(  # noqa: SLF001
                worker_cursor,
                task_id=task_id,
                lease_token=str(claim["lease_token"]),
            )
        )
        await asyncio.sleep(0.1)

        await updater_cursor.execute(
            "UPDATE knowledge_build_task SET updated_at=NOW() WHERE kid=%(kid)s",
            {"kid": task_id},
        )
        await updater.commit()
        locked_task, locked_file = await asyncio.wait_for(worker_lock, timeout=8)
        assert int(locked_task["kid"]) == task_id
        assert int(locked_file["kid"]) == harness.file_ids["/docs/a.txt"]
    finally:
        await worker.rollback()
        await updater.rollback()
        await worker.close()
        await updater.close()
        await _cleanup(harness)
