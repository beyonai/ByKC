"""Durable KnowledgeEntity runner coverage with real infrastructure.

Only the model outcome is deterministic. OpenGauss, MinIO, Redis, migration
execution, task claiming, lease reaping, and callback timing use production
implementations or real services.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import redis.asyncio as redis
from psycopg import sql

from by_qa.config import get_settings
from by_qa.knowledge_base.events import KnowledgeEventPublisherInvoker
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.storage_s3 import build_s3_storage_provider
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_batch_repository import (
    KnowledgeSemanticProcessingBatchRepository,
)
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_task_repository import (
    KnowledgeSemanticProcessingTaskRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.knowledge_entity_background_runner import (
    KnowledgeEntityBackgroundRunner,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class DeterministicModelWorker:
    """Model double that still reads every task input from real MinIO."""

    def __init__(self, storage):
        self.storage = storage
        self.calls = []

    async def run_task(self, context):
        location = self.storage.build_markdown_location(
            kb_code=context.kb_code,
            knowledge_base_id=context.knowledge_base_id,
            fs_entry_id=context.source_file_id,
            file_path=context.file_path,
        )
        content = await self.storage.read(location)
        self.calls.append(context.task_id)
        if b"MODEL_FAIL" in content:
            raise RuntimeError("deterministic model failure")
        return {
            "resultPayload": {"model": "mock", "inputBytes": len(content)},
            "indexVersion": "mock-model/1",
        }


class Publisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)

    @property
    def files(self):
        return [event for event in self.events if ".file.completed" in event.event_type]

    @property
    def batches(self):
        return [
            event for event in self.events if ".batch.completed" in event.event_type
        ]


async def test_real_services_support_claim_failure_progress_and_lease_reaping():
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    schema_name = f"entity_runner_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(
        update={"db_schema": schema_name, "embedding_dimension": 3}
    )
    connection_factory = build_connection_factory(settings)
    task_repository = KnowledgeSemanticProcessingTaskRepository()
    batch_repository = KnowledgeSemanticProcessingBatchRepository()
    storage = build_s3_storage_provider(settings)
    callback = Publisher()
    object_locations = []

    redis_client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        username=settings.redis_username or None,
        password=settings.redis_password or None,
        db=settings.redis_database,
    )
    try:
        setup = await connection_factory()
        try:
            await KnowledgeBaseSchemaBootstrapService(
                embedding_model_name="mock-embedding",
                embedding_dimension=3,
            ).apply(setup)
        finally:
            await setup.close()
        await storage.ensure_ready()
        assert await redis_client.ping() is True

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                INSERT INTO knowledge_base (kb_name)
                VALUES (%(name)s)
                RETURNING kid
                """,
                {"name": f"entity runner integration {uuid4().hex}"},
            )
            knowledge_base_id = int((await cursor.fetchone())["kid"])
            file_ids = []
            for index in (1, 2):
                await cursor.execute(
                    """
                    INSERT INTO knowledge_fs_entry (
                        knowledge_base_id, entry_type, is_root, name,
                        path_ltree, depth, mime_type, checksum
                    )
                    VALUES (
                        %(knowledge_base_id)s, 'FILE', FALSE, %(name)s,
                        %(path)s::ltree, 1, 'text/markdown', %(checksum)s
                    )
                    RETURNING kid
                    """,
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "name": f"doc-{index}.md",
                        "path": f"doc_{index}",
                        "checksum": f"sha-{index}",
                    },
                )
                file_ids.append(int((await cursor.fetchone())["kid"]))
            batch_id = f"ed-{uuid4().hex}"
            await batch_repository.create_batch(
                cursor,
                batch_id=batch_id,
                knowledge_base_id=knowledge_base_id,
                task_type="ENTITY_DISCOVERY",
                scope="WHOLE_KB",
                total_count=2,
            )
            for index, file_id in enumerate(file_ids, start=1):
                await task_repository.create_processing_task(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=file_id,
                    task_type="ENTITY_DISCOVERY",
                    batch_id=batch_id,
                    file_path_snapshot=f"/doc-{index}.md",
                    input_fingerprint=f"fp-{index}",
                    request_params={"maxEntities": 12},
                )
            await connection.commit()
        finally:
            await connection.close()

        for index, file_id in enumerate(file_ids, start=1):
            location = storage.build_markdown_location(
                kb_code=str(knowledge_base_id),
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=file_id,
                file_path=f"/doc-{index}.md",
            )
            object_locations.append(location)
            await storage.write(
                location,
                b"MODEL_FAIL" if index == 2 else b"MODEL_SUCCESS",
                content_type="text/markdown",
            )

        worker = DeterministicModelWorker(storage)
        runner = KnowledgeEntityBackgroundRunner(
            connection_factory=connection_factory,
            task_repository=task_repository,
            batch_repository=batch_repository,
            worker=worker,
            event_publisher_invoker=KnowledgeEventPublisherInvoker(callback),
            worker_id="real-it-worker",
            concurrency=2,
            lease_seconds=10,
            heartbeat_seconds=1,
        )
        assert await runner.run_claim_cycle() == 2
        await asyncio.wait_for(asyncio.gather(*tuple(runner._active_tasks)), timeout=20)

        verify = await connection_factory()
        try:
            cursor = verify.cursor()
            await cursor.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM knowledge_semantic_processing_task
                WHERE batch_id = %(batch_id)s
                GROUP BY status
                """,
                {"batch_id": batch_id},
            )
            counts = {
                row["status"]: int(row["count"]) for row in await cursor.fetchall()
            }
            assert counts == {"succeeded": 1, "failed": 1}
            batch = await batch_repository.get_batch(
                cursor, batch_id=batch_id, knowledge_base_id=knowledge_base_id
            )
            assert batch["status"] == "completed"
            assert batch["completed_count"] == 2

            lease_batch_id = f"ed-{uuid4().hex}"
            await batch_repository.create_batch(
                cursor,
                batch_id=lease_batch_id,
                knowledge_base_id=knowledge_base_id,
                task_type="ENTITY_DISCOVERY",
                scope="SINGLE_FILE",
                total_count=1,
            )
            expired = await task_repository.create_processing_task(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=file_ids[0],
                task_type="ENTITY_DISCOVERY",
                status="running",
                batch_id=lease_batch_id,
                file_path_snapshot="/doc-1.md",
            )
            await cursor.execute(
                """
                UPDATE knowledge_semantic_processing_task
                SET worker_id = 'killed-worker',
                    lease_token = 'expired-token',
                    heartbeat_at = %(expired_at)s,
                    lease_expires_at = %(expired_at)s
                WHERE kid = %(task_id)s
                """,
                {
                    "task_id": expired["kid"],
                    "expired_at": datetime.now(timezone.utc) - timedelta(minutes=1),
                },
            )
            await verify.commit()
        finally:
            await verify.close()

        assert await runner.run_reaper_cycle() == 1
        final_connection = await connection_factory()
        try:
            cursor = final_connection.cursor()
            reaped = await task_repository.get_task(cursor, task_id=int(expired["kid"]))
            assert reaped["status"] == "failed"
            assert reaped["error_code"] == "WORKER_LOST"
            assert reaped["outcome_uncertain"] is True
        finally:
            await final_connection.close()

        assert set(worker.calls) == set(file_ids)
        assert len(callback.files) == 3
        assert len(callback.batches) == 2
    finally:
        await redis_client.aclose()
        for location in object_locations:
            await storage.delete_quietly(location)
        cleanup_settings = base_settings.model_copy(update={"db_schema": ""})
        cleanup = await build_connection_factory(cleanup_settings)()
        try:
            await cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup.commit()
        finally:
            await cleanup.close()
