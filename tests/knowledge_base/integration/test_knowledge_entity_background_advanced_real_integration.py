"""Advanced real-service coverage for durable KnowledgeEntity scheduling."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as redis
from fastapi import FastAPI
from psycopg import sql

from by_qa.config import get_settings
from by_qa.knowledge_base.api.routes import register_routes
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.storage_s3 import build_s3_storage_provider
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_entity_repository import (
    KnowledgeEntityRepository,
)
from by_qa.knowledge_base.repositories.knowledge_file_reference_repository import (
    KnowledgeFileReferenceRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_search_repository import (
    KnowledgeItemSearchRepository,
)
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_batch_repository import (
    KnowledgeSemanticProcessingBatchRepository,
)
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_task_repository import (
    KnowledgeSemanticProcessingTaskRepository,
)
from by_qa.knowledge_base.repositories.metadata_search_repository import (
    MetadataSearchRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.knowledge_entity_background_runner import (
    KnowledgeEntityBackgroundRunner,
)
from by_qa.knowledge_base.services.knowledge_entity_callback import (
    KnowledgeEntityCallbackInvoker,
)
from by_qa.knowledge_base.services.knowledge_entity_processing_service import (
    KnowledgeEntityProcessingOrchestrator,
)
from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskContext,
    KnowledgeEntityTaskWorker,
)
from by_qa.knowledge_base.services.knowledge_item_search_service import (
    KnowledgeItemSearchService,
)
from by_qa.knowledge_base.services.markdown_reference_resolver import (
    MarkdownReferenceResolver,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class RecordingCallback:
    files: list = field(default_factory=list)
    batches: list = field(default_factory=list)

    async def on_file_completed(self, event):
        self.files.append(event)

    async def on_batch_completed(self, event):
        self.batches.append(event)


class StorageReadingModelWorker:
    """Model double; scheduling, storage reads, and persistence remain real."""

    def __init__(self, storage, calls: list[int]):
        self.storage = storage
        self.calls = calls

    async def run_task(self, context):
        location = self.storage.build_markdown_location(
            kb_code=context.kb_code,
            knowledge_base_id=context.knowledge_base_id,
            fs_entry_id=context.source_file_id,
            file_path=context.file_path,
        )
        content = await self.storage.read(location)
        self.calls.append(context.source_file_id)
        await asyncio.sleep(0.01)
        return {
            "resultPayload": {"inputBytes": len(content), "model": "mock"},
            "indexVersion": "mock-model/1",
        }


class DeterministicEmbeddingModel:
    async def embed_query(self, query):
        assert query
        return [0.0, 0.0, 0.0]


class FailingEnrichmentModel:
    def __init__(self):
        self.evidence = None

    async def enrich(self, identity, evidence, **kwargs):
        del identity, kwargs
        self.evidence = list(evidence)
        raise RuntimeError("deterministic enrichment model failure")


class ForbiddenMutationService:
    def __init__(self):
        self.called = False

    def __getattr__(self, name):
        async def forbidden(*args, **kwargs):
            del args, kwargs
            self.called = True
            raise AssertionError(f"mutation service must not be called: {name}")

        return forbidden


@dataclass
class RealEnvironment:
    base_settings: object
    settings: object
    schema_name: str
    connection_factory: object
    storage: object
    task_repository: KnowledgeSemanticProcessingTaskRepository
    batch_repository: KnowledgeSemanticProcessingBatchRepository
    object_locations: list = field(default_factory=list)


@pytest_asyncio.fixture(name="real_environment")
async def real_environment_fixture():
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    schema_name = f"entity_runner_advanced_it_{uuid4().hex[:12]}"
    settings = base_settings.model_copy(
        update={"db_schema": schema_name, "embedding_dimension": 3}
    )
    connection_factory = build_connection_factory(settings)
    storage = build_s3_storage_provider(settings)
    environment = RealEnvironment(
        base_settings=base_settings,
        settings=settings,
        schema_name=schema_name,
        connection_factory=connection_factory,
        storage=storage,
        task_repository=KnowledgeSemanticProcessingTaskRepository(),
        batch_repository=KnowledgeSemanticProcessingBatchRepository(),
    )
    redis_client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        username=settings.redis_username or None,
        password=settings.redis_password or None,
        db=settings.redis_database,
    )
    try:
        connection = await connection_factory()
        try:
            await KnowledgeBaseSchemaBootstrapService(
                embedding_model_name="mock-embedding",
                embedding_dimension=3,
            ).apply(connection)
        finally:
            await connection.close()
        await storage.ensure_ready()
        assert await redis_client.ping() is True
        yield environment
    finally:
        await redis_client.aclose()
        for location in environment.object_locations:
            await storage.delete_quietly(location)
        cleanup_settings = base_settings.model_copy(update={"db_schema": ""})
        cleanup = await build_connection_factory(cleanup_settings)()
        try:
            await cleanup.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
            await cleanup.commit()
        finally:
            await cleanup.close()


async def seed_files(
    environment: RealEnvironment,
    *,
    count: int,
    create_batch: bool = True,
    extra_params: dict | None = None,
):
    connection = await environment.connection_factory()
    try:
        cursor = connection.cursor()
        await cursor.execute(
            "INSERT INTO knowledge_base (kb_name) VALUES (%(name)s) RETURNING kid",
            {"name": f"advanced runner integration {uuid4().hex}"},
        )
        knowledge_base_id = int((await cursor.fetchone())["kid"])
        files = []
        for index in range(1, count + 1):
            file_path = f"/docs/document-{index}.md"
            await cursor.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, entry_type, is_root, name, path_ltree,
                    virtual_path, depth, mime_type, checksum, line_count,
                    markdown_bucket_name, markdown_object_key
                )
                VALUES (
                    %(knowledge_base_id)s, 'FILE', FALSE, %(name)s,
                    %(path_ltree)s::ltree, %(file_path)s, 1, 'text/markdown',
                    %(checksum)s, 1, %(bucket)s, %(object_key)s
                )
                RETURNING kid
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "name": f"document-{index}.md",
                    "path_ltree": f"document_{index}",
                    "file_path": file_path,
                    "checksum": f"sha-{index}",
                    "bucket": environment.storage.storage.markdown_bucket_name,
                    "object_key": f"placeholder-{index}",
                },
            )
            file_id = int((await cursor.fetchone())["kid"])
            files.append((file_id, file_path))
        batch_id = f"ed-{uuid4().hex}"
        if create_batch:
            await environment.batch_repository.create_batch(
                cursor,
                batch_id=batch_id,
                knowledge_base_id=knowledge_base_id,
                task_type="ENTITY_DISCOVERY",
                scope="WHOLE_KB",
                total_count=count,
                extra_params=extra_params,
            )
            for file_id, file_path in files:
                await environment.task_repository.create_processing_task(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=file_id,
                    task_type="ENTITY_DISCOVERY",
                    batch_id=batch_id,
                    file_path_snapshot=file_path,
                    input_fingerprint=f"fp-{file_id}",
                    input_checksum=f"sha-{file_id}",
                    request_params={"maxEntities": 12},
                    extra_params=extra_params,
                )
        await connection.commit()
    finally:
        await connection.close()

    for file_id, file_path in files:
        location = environment.storage.build_markdown_location(
            kb_code=str(knowledge_base_id),
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=file_id,
            file_path=file_path,
        )
        environment.object_locations.append(location)
        await environment.storage.write(
            location,
            f"model input for {file_id}".encode(),
            content_type="text/markdown",
        )
    storage_connection = await environment.connection_factory()
    try:
        cursor = storage_connection.cursor()
        for location, (file_id, _) in zip(
            environment.object_locations[-len(files) :], files, strict=True
        ):
            await cursor.execute(
                """
                UPDATE knowledge_fs_entry
                SET markdown_bucket_name = %(bucket)s,
                    markdown_object_key = %(object_key)s
                WHERE kid = %(file_id)s
                """,
                {
                    "bucket": location.namespace,
                    "object_key": location.key,
                    "file_id": file_id,
                },
            )
        await storage_connection.commit()
    finally:
        await storage_connection.close()
    return knowledge_base_id, batch_id, files


def build_runner(
    environment: RealEnvironment,
    *,
    worker_id: str,
    worker,
    callback_invoker,
    concurrency: int = 4,
    lease_seconds: int = 10,
):
    return KnowledgeEntityBackgroundRunner(
        connection_factory=environment.connection_factory,
        task_repository=environment.task_repository,
        batch_repository=environment.batch_repository,
        worker=worker,
        callback_invoker=callback_invoker,
        worker_id=worker_id,
        concurrency=concurrency,
        poll_seconds=0.05,
        lease_seconds=lease_seconds,
        heartbeat_seconds=0.2,
        reaper_seconds=0.1,
        shutdown_grace_seconds=1,
    )


async def test_two_independent_runners_claim_each_file_exactly_once(real_environment):
    knowledge_base_id, batch_id, files = await seed_files(
        real_environment, count=12, extra_params={"requestId": "race-it"}
    )
    calls = []
    callback = RecordingCallback()
    invoker = KnowledgeEntityCallbackInvoker(callback)
    first = build_runner(
        real_environment,
        worker_id="runner-a",
        worker=StorageReadingModelWorker(real_environment.storage, calls),
        callback_invoker=invoker,
        concurrency=12,
    )
    second = build_runner(
        real_environment,
        worker_id="runner-b",
        worker=StorageReadingModelWorker(real_environment.storage, calls),
        callback_invoker=invoker,
        concurrency=12,
    )

    claimed = await asyncio.gather(first.run_claim_cycle(), second.run_claim_cycle())
    await asyncio.gather(
        *tuple(first._active_tasks),
        *tuple(second._active_tasks),
    )

    assert sum(claimed) == 12
    assert Counter(calls) == Counter({file_id: 1 for file_id, _ in files})
    connection = await real_environment.connection_factory()
    try:
        cursor = connection.cursor()
        counts = await real_environment.batch_repository.count_tasks_by_status(
            cursor, batch_id=batch_id
        )
        batch = await real_environment.batch_repository.get_batch(
            cursor,
            batch_id=batch_id,
            knowledge_base_id=knowledge_base_id,
        )
    finally:
        await connection.close()
    assert counts == {"succeeded": 12}
    assert batch["completed_count"] == 12
    assert batch["status"] == "completed"
    assert len(callback.files) == 12
    assert len(callback.batches) == 1


async def test_expired_claim_is_failed_and_stale_worker_cannot_overwrite_result(
    real_environment,
):
    knowledge_base_id, batch_id, _ = await seed_files(real_environment, count=1)
    callback = RecordingCallback()
    invoker = KnowledgeEntityCallbackInvoker(callback)
    abandoned = build_runner(
        real_environment,
        worker_id="killed-runner",
        worker=object(),
        callback_invoker=invoker,
        lease_seconds=1,
    )
    reaper = build_runner(
        real_environment,
        worker_id="replacement-runner",
        worker=object(),
        callback_invoker=invoker,
        lease_seconds=1,
    )

    claimed = await abandoned._claim_one()
    assert claimed is not None
    await asyncio.sleep(1.2)
    assert await reaper.run_reaper_cycle() == 1

    await abandoned._finish_claimed(
        claimed,
        lease_token=str(claimed["lease_token"]),
        status="succeeded",
        result_payload={"mustNotPersist": True},
    )

    connection = await real_environment.connection_factory()
    try:
        cursor = connection.cursor()
        task = await real_environment.task_repository.get_task(
            cursor, task_id=int(claimed["kid"])
        )
        batch = await real_environment.batch_repository.get_batch(
            cursor,
            batch_id=batch_id,
            knowledge_base_id=knowledge_base_id,
        )
    finally:
        await connection.close()
    assert task["status"] == "failed"
    assert task["error_code"] == "WORKER_LOST"
    assert task["result_payload"] is None
    assert batch["completed_count"] == 1
    assert len(callback.files) == 1
    assert len(callback.batches) == 1


async def test_http_acceptance_runs_in_background_and_reports_batch_progress(
    real_environment,
):
    knowledge_base_id, _, files = await seed_files(
        real_environment, count=1, create_batch=False
    )
    calls = []
    callback = RecordingCallback()
    invoker = KnowledgeEntityCallbackInvoker(callback)
    worker = StorageReadingModelWorker(real_environment.storage, calls)
    runner = build_runner(
        real_environment,
        worker_id="http-runner",
        worker=worker,
        callback_invoker=invoker,
        concurrency=2,
    )
    service = KnowledgeEntityProcessingOrchestrator(
        connection_factory=real_environment.connection_factory,
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_entity_repository=KnowledgeEntityRepository(),
        knowledge_semantic_processing_task_repository=(
            real_environment.task_repository
        ),
        knowledge_semantic_processing_batch_repository=(
            real_environment.batch_repository
        ),
        knowledge_file_reference_repository=KnowledgeFileReferenceRepository(),
        worker=worker,
        callback_invoker=invoker,
        background_runner=runner,
    )
    app = FastAPI()

    async def entity_service():
        return service

    async def unused_service():
        return object()

    register_routes(
        app,
        get_knowledge_base_service=unused_service,
        get_knowledge_item_ingestion_service=unused_service,
        get_knowledge_item_search_service=unused_service,
        get_document_chunking_service=unused_service,
        get_metadata_search_service=unused_service,
        get_file_metadata_query_service=unused_service,
        get_knowledge_entity_processing_service=entity_service,
    )
    transport = httpx.ASGITransport(app=app)
    await service.start()
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://integration.test"
        ) as client:
            accepted_response = await client.post(
                "/api/v1/knowledgeItems/entityDiscovery",
                json={
                    "knCode": str(knowledge_base_id),
                    "filePath": files[0][1],
                    "extraParams": {"requestId": "http-it"},
                },
            )
            accepted = accepted_response.json()
            assert accepted["resultCode"] == "0", accepted
            batch_id = accepted["resultObject"]["batchId"]

            result = None
            for _ in range(100):
                status_response = await client.post(
                    "/api/v1/knowledgeItems/processingBatchStatus",
                    json={
                        "knCode": str(knowledge_base_id),
                        "batchId": batch_id,
                        "includeDetails": True,
                    },
                )
                result = status_response.json()["resultObject"]
                if result.get("status") == "COMPLETED":
                    break
                await asyncio.sleep(0.05)
    finally:
        await service.stop()

    assert result["status"] == "COMPLETED"
    assert result["succeededCount"] == 1
    assert result["completedCount"] == 1
    assert result["progress"] == 100
    assert result["extraParams"] == {"requestId": "http-it"}
    assert result["data"][0]["extraParams"] == {"requestId": "http-it"}
    assert calls == [files[0][0]]
    assert callback.files[0].extra_params == {"requestId": "http-it"}
    assert callback.batches[0].progress.completed_count == 1


async def test_real_enrich_worker_model_failure_keeps_minio_and_db_unchanged(
    real_environment,
):
    knowledge_base_id, _, files = await seed_files(
        real_environment, count=2, create_batch=False
    )
    entity_id, _ = files[0]
    evidence_id, evidence_path = files[1]
    entity_path = "/KnowledgeEntity/entity.md"
    entity_location, evidence_location = real_environment.object_locations[-2:]
    original_content = b"# Entity\n\nOriginal content that must survive."
    await real_environment.storage.write(
        entity_location, original_content, content_type="text/markdown"
    )
    await real_environment.storage.write(
        evidence_location,
        b"# Evidence\n\nEntity has a verified fact.",
        content_type="text/markdown",
    )

    reference_repository = KnowledgeFileReferenceRepository()
    connection = await real_environment.connection_factory()
    try:
        cursor = connection.cursor()
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET name = 'entity.md', virtual_path = %(entity_path)s
            WHERE kid = %(entity_id)s
            """,
            {"entity_path": entity_path, "entity_id": entity_id},
        )
        for property_name, value in (
            ("documentKind", "knowledgeEntity"),
            ("entityName", "Entity"),
        ):
            await cursor.execute(
                """
                INSERT INTO knowledge_file_metadata_value (
                    fs_entry_id, knowledge_base_id, property_name, value_type,
                    value_string, is_deleted
                )
                VALUES (
                    %(file_id)s, %(knowledge_base_id)s, %(property_name)s,
                    'string', %(value)s, FALSE
                )
                """,
                {
                    "file_id": entity_id,
                    "knowledge_base_id": knowledge_base_id,
                    "property_name": property_name,
                    "value": value,
                },
            )
        await reference_repository.upsert_relation_assertion(
            cursor,
            knowledge_base_id=knowledge_base_id,
            source_fs_entry_id=evidence_id,
            target_fs_entry_id=entity_id,
            original_target="Entity",
            relation_code="MENTIONS",
            discovered_by="MARKDOWN_PARSER",
            target_locator_type="KB_PATH",
            target_locator_value=entity_path,
        )
        await connection.commit()
        await cursor.execute(
            "SELECT checksum FROM knowledge_fs_entry WHERE kid = %(file_id)s",
            {"file_id": entity_id},
        )
        checksum_before = (await cursor.fetchone())["checksum"]
    finally:
        await connection.close()

    search = KnowledgeItemSearchService(
        connection_factory=real_environment.connection_factory,
        search_repository=KnowledgeItemSearchRepository(
            "chunk_embedding_mock_embedding"
        ),
        embedding_query_service=DeterministicEmbeddingModel(),
        metadata_search_repository=MetadataSearchRepository(),
        markdown_reference_resolver=MarkdownReferenceResolver(
            connection_factory=real_environment.connection_factory,
            reference_repository=reference_repository,
        ),
    )
    enrichment_model = FailingEnrichmentModel()
    forbidden_update = ForbiddenMutationService()
    forbidden_ingestion = ForbiddenMutationService()
    worker = KnowledgeEntityTaskWorker(
        connection_factory=real_environment.connection_factory,
        knowledge_entity_repository=KnowledgeEntityRepository(),
        knowledge_file_reference_repository=reference_repository,
        storage_provider=real_environment.storage,
        knowledge_item_ingestion_service=forbidden_ingestion,
        document_update_service=forbidden_update,
        document_chunking_service=object(),
        knowledge_item_search_service=search,
        knowledge_entity_discovery=object(),
        knowledge_entity_enricher=enrichment_model,
    )

    with pytest.raises(RuntimeError, match="deterministic enrichment model failure"):
        await worker.run_task(
            KnowledgeEntityTaskContext(
                task_id=9001,
                task_type="DOCUMENT_ENRICH",
                kb_code=str(knowledge_base_id),
                knowledge_base_id=knowledge_base_id,
                source_file_id=entity_id,
                file_path=entity_path,
                input_checksum=checksum_before,
                request_params={"topK": 5},
                batch_id="enrich-failure-it",
            )
        )

    assert enrichment_model.evidence
    assert any(
        item.document_file_id == evidence_id and item.document_path == evidence_path
        for item in enrichment_model.evidence
    )
    assert forbidden_update.called is False
    assert forbidden_ingestion.called is False
    assert await real_environment.storage.read(entity_location) == original_content
    verify = await real_environment.connection_factory()
    try:
        cursor = verify.cursor()
        await cursor.execute(
            "SELECT checksum FROM knowledge_fs_entry WHERE kid = %(file_id)s",
            {"file_id": entity_id},
        )
        assert (await cursor.fetchone())["checksum"] == checksum_before
    finally:
        await verify.close()
