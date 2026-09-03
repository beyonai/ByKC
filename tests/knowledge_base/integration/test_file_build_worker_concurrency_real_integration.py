"""Real OpenGauss concurrency coverage for independent file Build workers."""

from __future__ import annotations

import asyncio
from collections import Counter
from uuid import uuid4

import pytest
from psycopg import sql

from by_qa.config import get_settings
from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest
from by_qa.knowledge_base.events import KnowledgeEventPublisherInvoker
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.runtime import (
    build_knowledge_item_ingestion_service,
)
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
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.file_build_background_runner import (
    FileBuildBackgroundRunner,
)
from by_qa.knowledge_base.services.file_build_models import (
    EmbeddingBuildProfile,
    FileBuildProfile,
)
from by_qa.knowledge_base.services.file_build_processing_service import (
    FileBuildProcessingService,
)
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _GatedExecutionService:
    """Keep claimed work active so per-instance capacity can be observed."""

    def __init__(self):
        self.started: list[dict] = []
        self.release = asyncio.Event()

    async def execute_claimed(self, row):
        self.started.append(row)
        await self.release.wait()

    async def finish_claimed(self, row, **kwargs):  # pragma: no cover - defensive
        raise AssertionError((row, kwargs))

    async def reap_one_expired(self):  # pragma: no cover - not used by this test
        return None


class _EmbeddingConfigProvider:
    async def get_config(self, model_type: str) -> ModelConfig:
        assert model_type == "embedding"
        return ModelConfig(
            model_name="file-build-inline",
            temperature=0.0,
            base_url="https://embedding.invalid",
            api_key="integration-test",
            dimension=3,
            distance_metric="cosine",
        )


class _EchoChunkingService:
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
                embedding=[0.1, 0.2, 0.3],
            )
        ]


class _RecordingPublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


async def _wait_for_started(execution: _GatedExecutionService, count: int) -> None:
    for _ in range(200):
        if len(execution.started) >= count:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"expected {count} started tasks, got {len(execution.started)}")


async def test_two_workers_claim_uniquely_and_each_instance_admits_sixteen_tasks():
    """SKIP LOCKED prevents duplicate claims and concurrency is per Worker instance."""
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    schema_name = f"file_build_workers_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(
        update={"db_schema": schema_name, "embedding_dimension": 3}
    )
    connection_factory = build_connection_factory(settings)
    task_repository = KnowledgeBuildTaskRepository()
    batch_repository = KnowledgeBuildBatchRepository()
    processing_service = FileBuildProcessingService(
        connection_factory=connection_factory,
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        acceptance_repository=KnowledgeBuildAcceptanceRepository(
            "chunk_embedding_file_build_workers"
        ),
        batch_repository=batch_repository,
        task_repository=task_repository,
        build_profile=FileBuildProfile(
            embedding=EmbeddingBuildProfile(model="file-build-workers", dimension=3)
        ),
    )
    execution = _GatedExecutionService()
    runner_a = FileBuildBackgroundRunner(
        connection_factory=connection_factory,
        task_repository=task_repository,
        batch_repository=batch_repository,
        execution_service=execution,
        worker_id="file-build-worker-a",
        concurrency=16,
        lease_seconds=30,
        heartbeat_seconds=1,
    )
    runner_b = FileBuildBackgroundRunner(
        connection_factory=connection_factory,
        task_repository=task_repository,
        batch_repository=batch_repository,
        execution_service=execution,
        worker_id="file-build-worker-b",
        concurrency=16,
        lease_seconds=30,
        heartbeat_seconds=1,
    )
    active_tasks: list[asyncio.Task] = []
    try:
        setup = await connection_factory()
        try:
            await KnowledgeBaseSchemaBootstrapService(
                embedding_model_name="file-build-workers",
                embedding_dimension=3,
            ).apply(setup)
        finally:
            await setup.close()

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "INSERT INTO knowledge_base (kb_name) VALUES (%(name)s) RETURNING kid",
                {"name": f"worker-concurrency-{uuid4().hex}"},
            )
            knowledge_base_id = int((await cursor.fetchone())["kid"])
            await cursor.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, entry_type, is_root, name, path_ltree,
                    depth, virtual_path
                )
                VALUES (
                    %(knowledge_base_id)s, 'DIRECTORY', FALSE, 'bulk',
                    'd1_bulk'::ltree, 1, '/bulk'
                )
                RETURNING kid
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            directory_id = int((await cursor.fetchone())["kid"])
            for index in range(17):
                await cursor.execute(
                    """
                    INSERT INTO knowledge_fs_entry (
                        knowledge_base_id, parent_entry_id, entry_type, is_root,
                        name, path_ltree, depth, virtual_path, mime_type,
                        checksum, file_bucket_name, file_object_key
                    )
                    VALUES (
                        %(knowledge_base_id)s, %(directory_id)s, 'FILE', FALSE,
                        %(name)s, %(path_ltree)s::ltree, 2, %(virtual_path)s,
                        'text/plain', %(checksum)s, 'source', %(object_key)s
                    )
                    """,
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "directory_id": directory_id,
                        "name": f"file-{index:02d}.txt",
                        "path_ltree": f"d1_bulk.f2_file_{index:02d}",
                        "virtual_path": f"/bulk/file-{index:02d}.txt",
                        "checksum": f"checksum-{index:02d}",
                        "object_key": f"file-{index:02d}.txt",
                    },
                )
            await connection.commit()
        finally:
            await connection.close()

        accepted = await processing_service.accept(
            FileToMarkdownIndexRequest(knCode=str(knowledge_base_id), filePath="/bulk")
        )
        assert accepted["acceptedCount"] == 17

        assert await runner_a.run_claim_cycle() == 16
        await _wait_for_started(execution, 16)
        assert len(runner_a._active_tasks) == 16  # noqa: SLF001
        assert await runner_a.run_claim_cycle() == 0

        assert await runner_b.run_claim_cycle() == 1
        await _wait_for_started(execution, 17)
        assert len(runner_b._active_tasks) == 1  # noqa: SLF001
        active_tasks = [*runner_a._active_tasks, *runner_b._active_tasks]  # noqa: SLF001

        claimed_ids = [int(row["kid"]) for row in execution.started]
        assert len(claimed_ids) == len(set(claimed_ids)) == 17
        assert Counter(row["worker_id"] for row in execution.started) == {
            "file-build-worker-a": 16,
            "file-build-worker-b": 1,
        }
        assert len({row["lease_token"] for row in execution.started}) == 17

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                SELECT status, worker_id, lease_token
                FROM knowledge_build_task
                WHERE batch_id = %(batch_id)s
                ORDER BY kid
                """,
                {"batch_id": accepted["batchId"]},
            )
            persisted = list(await cursor.fetchall())
            wrong_token_result = await task_repository.finish_claimed_task(
                cursor,
                task_id=claimed_ids[0],
                lease_token="not-the-owner-token",
                status="succeeded",
                result_payload={"lineCount": 1, "chunkCount": 1},
            )
            await connection.commit()
        finally:
            await connection.close()
        assert len(persisted) == 17
        assert {row["status"] for row in persisted} == {"running"}
        assert all(row["worker_id"] for row in persisted)
        assert all(row["lease_token"] for row in persisted)
        assert wrong_token_result is None
    finally:
        execution.release.set()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        cleanup = await connection_factory()
        try:
            await cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup.commit()
        finally:
            await cleanup.close()


async def test_entity_inline_build_completes_while_background_build_remains_pending():
    """The exact Entity call boundary bypasses the independent Build worker queue."""
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    schema_name = f"file_build_inline_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(
        update={
            "db_schema": schema_name,
            "embedding_model_name": "file-build-inline",
            "embedding_dimension": 3,
            "knowledge_build_worker_enabled": False,
        }
    )
    connection_factory = build_connection_factory(settings)
    publisher = _RecordingPublisher()
    chunker = _EchoChunkingService()
    ingestion = None
    stored_locations = []
    try:
        setup = await connection_factory()
        try:
            await KnowledgeBaseSchemaBootstrapService(
                embedding_model_name="file-build-inline",
                embedding_dimension=3,
            ).apply(setup)
        finally:
            await setup.close()
        ingestion = await build_knowledge_item_ingestion_service(
            settings,
            provider=_EmbeddingConfigProvider(),
            event_publisher_invoker=KnowledgeEventPublisherInvoker(publisher=publisher),
            document_chunking_service=chunker,
        )

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "INSERT INTO knowledge_base (kb_name) VALUES (%(name)s) RETURNING kid",
                {"name": f"inline-isolation-{uuid4().hex}"},
            )
            knowledge_base_id = int((await cursor.fetchone())["kid"])
            file_ids = {}
            for index, name in enumerate(("backlog.txt", "entity.txt"), start=1):
                await cursor.execute(
                    """
                    INSERT INTO knowledge_fs_entry (
                        knowledge_base_id, entry_type, is_root, name, path_ltree,
                        depth, virtual_path, mime_type, checksum
                    )
                    VALUES (
                        %(knowledge_base_id)s, 'FILE', FALSE, %(name)s,
                        %(path_ltree)s::ltree, 1, %(virtual_path)s,
                        'text/plain', %(checksum)s
                    )
                    RETURNING kid
                    """,
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "name": name,
                        "path_ltree": f"f1_{index}",
                        "virtual_path": f"/{name}",
                        "checksum": f"checksum-{name}",
                    },
                )
                file_id = int((await cursor.fetchone())["kid"])
                file_ids[name] = file_id
                location = ingestion.storage_provider.build_original_location(
                    kb_code=str(knowledge_base_id),
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=file_id,
                    file_path=f"/{name}",
                    mime_type="text/plain",
                )
                await ingestion.storage_provider.write(
                    location, f"content for {name}".encode(), content_type="text/plain"
                )
                stored_locations.append(location)
                stored_locations.append(
                    ingestion.storage_provider.build_markdown_location(
                        kb_code=str(knowledge_base_id),
                        knowledge_base_id=knowledge_base_id,
                        fs_entry_id=file_id,
                        file_path=f"/{name}",
                    )
                )
                await cursor.execute(
                    """
                    UPDATE knowledge_fs_entry
                    SET file_bucket_name = %(bucket)s,
                        file_object_key = %(object_key)s
                    WHERE kid = %(file_id)s
                    """,
                    {
                        "bucket": location.namespace,
                        "object_key": location.key,
                        "file_id": file_id,
                    },
                )
            semantic_batch_id = f"entity-inline-{uuid4().hex}"
            await cursor.execute(
                """
                INSERT INTO knowledge_semantic_processing_batch (
                    batch_id, knowledge_base_id, task_type, scope, status,
                    total_count, completed_count
                )
                VALUES (
                    %(batch_id)s, %(knowledge_base_id)s, 'ENTITY_DISCOVERY',
                    'SINGLE_FILE', 'processing', 1, 0
                )
                """,
                {
                    "batch_id": semantic_batch_id,
                    "knowledge_base_id": knowledge_base_id,
                },
            )
            await cursor.execute(
                """
                INSERT INTO knowledge_semantic_processing_task (
                    knowledge_base_id, fs_entry_id, task_type, batch_id,
                    file_path_snapshot, input_checksum, status, progress
                )
                VALUES (
                    %(knowledge_base_id)s, %(file_id)s, 'ENTITY_DISCOVERY',
                    %(batch_id)s, '/entity.txt', 'checksum-entity.txt',
                    'running', 10
                )
                RETURNING kid
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "file_id": file_ids["entity.txt"],
                    "batch_id": semantic_batch_id,
                },
            )
            parent_task_id = int((await cursor.fetchone())["kid"])
            await connection.commit()
        finally:
            await connection.close()

        background = await ingestion.accept_file_to_markdown_index(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/backlog.txt"
            )
        )
        assert background["acceptedCount"] == 1

        await ingestion.file_to_markdown_index(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/entity.txt"
            ),
            document_chunking_service=chunker,
            parent_semantic_task_id=parent_task_id,
            origin="ENTITY_DISCOVERY",
        )

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                SELECT fs_entry_id, batch_id, origin, execution_mode,
                       parent_semantic_task_id, status
                FROM knowledge_build_task
                ORDER BY kid
                """
            )
            tasks = list(await cursor.fetchall())
        finally:
            await connection.close()
        assert tasks == [
            {
                "fs_entry_id": file_ids["backlog.txt"],
                "batch_id": background["batchId"],
                "origin": "API",
                "execution_mode": "BACKGROUND",
                "parent_semantic_task_id": None,
                "status": "pending",
            },
            {
                "fs_entry_id": file_ids["entity.txt"],
                "batch_id": None,
                "origin": "ENTITY_DISCOVERY",
                "execution_mode": "INLINE",
                "parent_semantic_task_id": parent_task_id,
                "status": "succeeded",
            },
        ]
        assert not [
            event for event in publisher.events if event.event_type.startswith("build.")
        ]
    finally:
        if ingestion is not None:
            for location in stored_locations:
                await ingestion.storage_provider.delete_quietly(location)
        cleanup = await connection_factory()
        try:
            await cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup.commit()
        finally:
            await cleanup.close()
