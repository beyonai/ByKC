"""Real storage and OpenGauss coverage for the independent Build runner."""

from __future__ import annotations

import asyncio
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
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_batch_repository import (
    KnowledgeSemanticProcessingBatchRepository,
)
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_task_repository import (
    KnowledgeSemanticProcessingTaskRepository,
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
from by_qa.knowledge_base.services.file_build_mutation_service import (
    FileBuildMutationService,
)
from by_qa.knowledge_base.services.file_build_processing_service import (
    FileBuildProcessingService,
)
from by_qa.knowledge_base.services.file_build_terminal_event_service import (
    FileBuildTerminalEventService,
)
from by_qa.knowledge_base.services.semantic_task_mutation_service import (
    SemanticTaskMutationService,
)
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class DeterministicChunkingService:
    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        assert file_type == "txt"
        return file_bytes.decode("utf-8")

    def chunk_and_embed(
        self, markdown_bytes: bytes, *, filename: str
    ) -> list[KnowledgeItemChunkPayload]:
        assert filename == "runner.md"
        text = markdown_bytes.decode("utf-8")
        return [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=2,
                chunk_text=text,
                embedding=[0.1, 0.2, 0.3],
            )
        ]


class RecordingPublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


async def test_runner_claims_and_commits_complete_file_build():
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    schema_name = f"file_build_runner_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(
        update={"db_schema": schema_name, "embedding_dimension": 3}
    )
    connection_factory = build_connection_factory(settings)
    storage = build_s3_storage_provider(settings)
    embedding_table_name = "chunk_embedding_file_build_runner"
    task_repository = KnowledgeBuildTaskRepository()
    batch_repository = KnowledgeBuildBatchRepository()
    fs_repository = KnowledgeFsEntryRepository()
    chunk_repository = KnowledgeItemChunkRepository(embedding_table_name)
    publisher = RecordingPublisher()
    terminal_event_service = FileBuildTerminalEventService(
        connection_factory=connection_factory,
        batch_repository=batch_repository,
        event_publisher_invoker=KnowledgeEventPublisherInvoker(publisher=publisher),
    )
    processing_service = FileBuildProcessingService(
        connection_factory=connection_factory,
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=fs_repository,
        acceptance_repository=KnowledgeBuildAcceptanceRepository(embedding_table_name),
        batch_repository=batch_repository,
        task_repository=task_repository,
        build_profile=FileBuildProfile(
            embedding=EmbeddingBuildProfile(model="file-build-runner", dimension=3)
        ),
        terminal_event_service=terminal_event_service,
    )
    execution_service = FileBuildExecutionService(
        connection_factory=connection_factory,
        task_repository=task_repository,
        batch_repository=batch_repository,
        fs_entry_repository=fs_repository,
        chunk_repository=chunk_repository,
        retrieval_repository=RetrievalProjectionRepository(),
        fetch_cache_repository=KnowledgeFetchCacheRepository(),
        storage_provider=storage,
        document_chunking_service=DeterministicChunkingService(),
        embedding_dimension=3,
        terminal_event_service=terminal_event_service,
    )
    runner = FileBuildBackgroundRunner(
        connection_factory=connection_factory,
        task_repository=task_repository,
        batch_repository=batch_repository,
        execution_service=execution_service,
        worker_id="file-build-runner-test",
        concurrency=16,
        lease_seconds=30,
        heartbeat_seconds=5,
    )
    mutation_service = FileBuildMutationService(
        task_repository=task_repository,
        batch_repository=batch_repository,
        terminal_event_service=terminal_event_service,
    )
    semantic_task_repository = KnowledgeSemanticProcessingTaskRepository()
    semantic_batch_repository = KnowledgeSemanticProcessingBatchRepository()
    semantic_mutation_service = SemanticTaskMutationService(
        task_repository=semantic_task_repository,
        batch_repository=semantic_batch_repository,
        event_publisher_invoker=KnowledgeEventPublisherInvoker(publisher=publisher),
    )
    original_location = None
    markdown_location = None
    extra_locations = []
    try:
        setup = await connection_factory()
        try:
            await KnowledgeBaseSchemaBootstrapService(
                embedding_model_name="file-build-runner",
                embedding_dimension=3,
            ).apply(setup)
        finally:
            await setup.close()
        await storage.ensure_ready()

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "INSERT INTO knowledge_base (kb_name) VALUES (%(name)s) RETURNING kid",
                {"name": f"runner-{uuid4().hex}"},
            )
            knowledge_base_id = int((await cursor.fetchone())["kid"])
            await cursor.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, entry_type, is_root, name, path_ltree,
                    depth, virtual_path, mime_type, checksum
                )
                VALUES (
                    %(knowledge_base_id)s, 'FILE', FALSE, 'runner.txt',
                    'f1_runner'::ltree, 1, '/runner.txt', 'text/plain', 'runner-sha'
                )
                RETURNING kid
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            file_id = int((await cursor.fetchone())["kid"])
            original_location = storage.build_original_location(
                kb_code=str(knowledge_base_id),
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=file_id,
                file_path="/runner.txt",
                mime_type="text/plain",
            )
            markdown_location = storage.build_markdown_location(
                kb_code=str(knowledge_base_id),
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=file_id,
                file_path="/runner.txt",
            )
            await storage.write(
                original_location,
                b"Runner content\nSecond line",
                content_type="text/plain",
            )
            await cursor.execute(
                """
                UPDATE knowledge_fs_entry
                SET file_bucket_name = %(bucket)s, file_object_key = %(object_key)s,
                    file_size = 26
                WHERE kid = %(file_id)s
                """,
                {
                    "bucket": original_location.namespace,
                    "object_key": original_location.key,
                    "file_id": file_id,
                },
            )
            await connection.commit()
        finally:
            await connection.close()

        accepted = await processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/runner.txt"
            )
        )
        assert accepted["acceptedCount"] == 1
        assert runner.concurrency == 16
        assert await runner.run_claim_cycle() == 1
        active = list(runner._active_tasks)  # noqa: SLF001
        await asyncio.gather(*active)

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "SELECT * FROM knowledge_build_task WHERE kid = %(task_id)s",
                {"task_id": int(accepted["tasks"][0]["taskId"])},
            )
            task = await cursor.fetchone()
            await cursor.execute(
                "SELECT * FROM knowledge_build_batch WHERE batch_id = %(batch_id)s",
                {"batch_id": accepted["batchId"]},
            )
            batch = await cursor.fetchone()
            await cursor.execute(
                """
                SELECT COUNT(*) AS chunk_count,
                       COUNT(embedding.chunk_id) AS embedding_count,
                       COUNT(retrieval.chunk_id) AS retrieval_count
                FROM knowledge_chunk chunk
                LEFT JOIN chunk_embedding_file_build_runner embedding
                  ON embedding.chunk_id = chunk.kid
                LEFT JOIN knowledge_chunk_retrieval_mv retrieval
                  ON retrieval.chunk_id = chunk.kid
                WHERE chunk.fs_entry_id = %(file_id)s
                """,
                {"file_id": file_id},
            )
            coverage = await cursor.fetchone()
        finally:
            await connection.close()

        assert task["status"] == "succeeded"
        assert task["current_stage"] == "committing"
        assert task["result_payload"] == {"lineCount": 2, "chunkCount": 1}
        assert task["worker_id"] is None
        assert batch["status"] == "completed"
        assert batch["completed_count"] == 1
        assert coverage == {
            "chunk_count": 1,
            "embedding_count": 1,
            "retrieval_count": 1,
        }
        assert await storage.read(markdown_location) == b"Runner content\nSecond line"
        assert [event.event_type for event in publisher.events] == [
            "build.file.completed",
            "build.batch.completed",
        ]
        assert publisher.events[0].event_version == 2
        assert publisher.events[0].payload.file_id == str(file_id)

        connection = await connection_factory()
        try:
            await connection.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, entry_type, is_root, name, path_ltree,
                    depth, virtual_path
                )
                VALUES (
                    %(knowledge_base_id)s, 'DIRECTORY', FALSE, 'empty',
                    'd1_empty'::ltree, 1, '/empty'
                )
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            await connection.commit()
        finally:
            await connection.close()
        empty_acceptance = await processing_service.accept(
            FileToMarkdownIndexRequest(knCode=str(knowledge_base_id), filePath="/empty")
        )
        assert empty_acceptance["acceptedCount"] == 0
        assert len(publisher.events) == 3
        assert publisher.events[-1].event_type == "build.batch.completed"

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            semantic_batch_id = f"entity-inline-{uuid4().hex}"
            await cursor.execute(
                """
                INSERT INTO knowledge_semantic_processing_batch (
                    batch_id, knowledge_base_id, task_type, scope, status,
                    total_count, completed_count
                )
                VALUES (
                    %(batch_id)s, %(knowledge_base_id)s, 'ENTITY_DISCOVERY',
                    'SINGLE_FILE', 'completed', 1, 1
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
                    file_path_snapshot, status, progress
                )
                VALUES (
                    %(knowledge_base_id)s, %(file_id)s, 'ENTITY_DISCOVERY',
                    %(batch_id)s, '/runner.txt', 'succeeded', 100
                )
                RETURNING kid
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "file_id": file_id,
                    "batch_id": semantic_batch_id,
                },
            )
            parent_semantic_task_id = int((await cursor.fetchone())["kid"])
            profile = processing_service.build_profile
            inline_task = await task_repository.create_inline_task(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=file_id,
                origin="ENTITY_DISCOVERY",
                parent_semantic_task_id=parent_semantic_task_id,
                file_path_snapshot="runner.txt",
                input_checksum="runner-sha",
                input_is_deleted=False,
                build_profile=profile.storage_value(),
                build_profile_hash=profile.sha256(),
            )
            await connection.commit()
        finally:
            await connection.close()
        assert inline_task is not None
        await execution_service.execute_inline(inline_task)
        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                SELECT status, origin, execution_mode, parent_semantic_task_id,
                       batch_id, result_payload
                FROM knowledge_build_task
                WHERE kid = %(kid)s
                """,
                {"kid": int(inline_task["kid"])},
            )
            persisted_inline = await cursor.fetchone()
        finally:
            await connection.close()
        assert persisted_inline == {
            "status": "succeeded",
            "origin": "ENTITY_DISCOVERY",
            "execution_mode": "INLINE",
            "parent_semantic_task_id": parent_semantic_task_id,
            "batch_id": None,
            "result_payload": {"lineCount": 2, "chunkCount": 1},
        }
        assert len(publisher.events) == 3

        async def add_source(name: str, checksum: str):
            connection = await connection_factory()
            try:
                cursor = connection.cursor()
                label = name.replace(".", "_").replace("-", "_")
                await cursor.execute(
                    """
                    INSERT INTO knowledge_fs_entry (
                        knowledge_base_id, entry_type, is_root, name, path_ltree,
                        depth, virtual_path, mime_type, checksum
                    )
                    VALUES (
                        %(knowledge_base_id)s, 'FILE', FALSE, %(name)s,
                        %(label)s::ltree, 1, %(path)s, 'text/plain', %(checksum)s
                    )
                    RETURNING kid
                    """,
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "name": name,
                        "label": f"f1_{label}",
                        "path": f"/{name}",
                        "checksum": checksum,
                    },
                )
                source_id = int((await cursor.fetchone())["kid"])
                location = storage.build_original_location(
                    kb_code=str(knowledge_base_id),
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=source_id,
                    file_path=f"/{name}",
                    mime_type="text/plain",
                )
                await storage.write(
                    location, b"stale content", content_type="text/plain"
                )
                extra_locations.append(location)
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
                        "file_id": source_id,
                    },
                )
                await connection.commit()
                return source_id
            finally:
                await connection.close()

        mutation_file_id = await add_source("mutation.txt", "mutation-sha")
        mutation_acceptance = await processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/mutation.txt"
            )
        )
        event_count_before_mutation = len(publisher.events)
        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            terminated, completed_batches = await mutation_service.terminate_active(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_ids=[mutation_file_id],
                error_code="INPUT_STALE",
                error_message="Source checksum changed during file update",
            )
            await connection.commit()
        finally:
            await connection.close()
        await mutation_service.publish(terminated, completed_batches)
        assert len(publisher.events) == event_count_before_mutation + 2
        assert publisher.events[-2].payload.status == "SKIPPED"
        assert publisher.events[-2].payload.error.code == "INPUT_STALE"
        assert publisher.events[-1].event_type == "build.batch.completed"
        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "SELECT status, error_code FROM knowledge_build_task WHERE kid = %(kid)s",
                {"kid": int(mutation_acceptance["tasks"][0]["taskId"])},
            )
            mutation_task = await cursor.fetchone()
        finally:
            await connection.close()
        assert mutation_task == {"status": "skipped", "error_code": "INPUT_STALE"}

        stale_file_id = await add_source("stale.txt", "stale-sha")
        stale_acceptance = await processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/stale.txt"
            )
        )
        stale_claim = await runner._claim_one()  # noqa: SLF001
        assert stale_claim is not None
        connection = await connection_factory()
        try:
            await connection.execute(
                "UPDATE knowledge_fs_entry SET checksum = 'changed' WHERE kid = %(kid)s",
                {"kid": stale_file_id},
            )
            await connection.commit()
        finally:
            await connection.close()
        await runner._execute_claimed(stale_claim)  # noqa: SLF001

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "SELECT status, error_code FROM knowledge_build_task WHERE kid = %(kid)s",
                {"kid": int(stale_acceptance["tasks"][0]["taskId"])},
            )
            stale_task = await cursor.fetchone()
        finally:
            await connection.close()
        assert stale_task == {"status": "skipped", "error_code": "INPUT_STALE"}

        lease_file_id = await add_source("lease.txt", "lease-sha")
        lease_acceptance = await processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/lease.txt"
            )
        )
        lease_claim = await runner._claim_one()  # noqa: SLF001
        assert lease_claim is not None
        connection = await connection_factory()
        try:
            await connection.execute(
                """
                UPDATE knowledge_build_task
                SET lease_expires_at = NOW() - INTERVAL '1 second'
                WHERE kid = %(kid)s
                """,
                {"kid": int(lease_acceptance["tasks"][0]["taskId"])},
            )
            await connection.commit()
        finally:
            await connection.close()
        await runner._execute_claimed(lease_claim)  # noqa: SLF001
        assert await runner.run_reaper_cycle() == 1

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "SELECT status, error_code FROM knowledge_build_task WHERE kid = %(kid)s",
                {"kid": int(lease_acceptance["tasks"][0]["taskId"])},
            )
            lease_task = await cursor.fetchone()
        finally:
            await connection.close()
        assert lease_task == {"status": "failed", "error_code": "WORKER_LOST"}
        assert lease_file_id > 0

        kb_delete_file_id = await add_source("kb-delete.txt", "kb-delete-sha")
        kb_delete_acceptance = await processing_service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/kb-delete.txt"
            )
        )
        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            semantic_batch_id = f"kb-delete-semantic-{uuid4().hex}"
            await semantic_batch_repository.create_batch(
                cursor,
                batch_id=semantic_batch_id,
                knowledge_base_id=knowledge_base_id,
                task_type="ENTITY_DISCOVERY",
                scope="SINGLE_FILE",
                total_count=1,
            )
            semantic_task = await semantic_task_repository.create_processing_task(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=kb_delete_file_id,
                task_type="ENTITY_DISCOVERY",
                batch_id=semantic_batch_id,
                file_path_snapshot="/kb-delete.txt",
                status="pending",
                progress=0,
            )
            terminated_build, completed_build = await mutation_service.terminate_active(
                cursor,
                knowledge_base_id=knowledge_base_id,
                error_code="KNOWLEDGE_BASE_DELETED",
                error_message="Knowledge base was deleted",
            )
            (
                terminated_semantic,
                completed_semantic,
            ) = await semantic_mutation_service.terminate_for_knowledge_base(
                cursor, knowledge_base_id=knowledge_base_id
            )
            await connection.commit()
        finally:
            await connection.close()
        await mutation_service.publish(terminated_build, completed_build)
        await semantic_mutation_service.publish(terminated_semantic, completed_semantic)
        assert semantic_task is not None
        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                SELECT status, error_code FROM knowledge_build_task
                WHERE kid = %(build_task_id)s
                """,
                {"build_task_id": int(kb_delete_acceptance["tasks"][0]["taskId"])},
            )
            deleted_build_task = await cursor.fetchone()
            await cursor.execute(
                """
                SELECT status, error_code FROM knowledge_semantic_processing_task
                WHERE kid = %(semantic_task_id)s
                """,
                {"semantic_task_id": int(semantic_task["kid"])},
            )
            deleted_semantic_task = await cursor.fetchone()
        finally:
            await connection.close()
        assert deleted_build_task == {
            "status": "skipped",
            "error_code": "KNOWLEDGE_BASE_DELETED",
        }
        assert deleted_semantic_task == {
            "status": "skipped",
            "error_code": "KNOWLEDGE_BASE_DELETED",
        }
        assert any(
            event.event_type == "semantic.discovery.batch.completed"
            for event in publisher.events
        )
    finally:
        if original_location is not None:
            await storage.delete_quietly(original_location)
        if markdown_location is not None:
            await storage.delete_quietly(markdown_location)
        for location in extra_locations:
            await storage.delete_quietly(location)
        cleanup = await connection_factory()
        try:
            await cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup.commit()
        finally:
            await cleanup.close()
