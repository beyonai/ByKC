"""Real OpenGauss coverage for set-based File Build acceptance."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from psycopg import sql

from by_qa.config import get_settings
from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    ProcessingBatchStatusRequest,
    ProcessingTaskStatusRequest,
    ProcessingTaskType,
)
from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
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
from by_qa.knowledge_base.repositories.processing_task_query_repository import (
    ProcessingTaskQueryRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.file_build_models import (
    EmbeddingBuildProfile,
    FileBuildProfile,
)
from by_qa.knowledge_base.services.file_build_processing_service import (
    FileBuildProcessingService,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_directory_acceptance_reuses_and_supersedes_by_stable_input():
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail("real OpenGauss configuration is required", pytrace=False)
    schema_name = f"file_build_accept_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(
        update={"db_schema": schema_name, "embedding_dimension": 3}
    )
    connection_factory = build_connection_factory(settings)
    embedding_table_name = "chunk_embedding_file_build_acceptance"
    service = FileBuildProcessingService(
        connection_factory=connection_factory,
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        acceptance_repository=KnowledgeBuildAcceptanceRepository(embedding_table_name),
        batch_repository=KnowledgeBuildBatchRepository(),
        task_repository=KnowledgeBuildTaskRepository(),
        build_profile=FileBuildProfile(
            embedding=EmbeddingBuildProfile(model="file-build-acceptance", dimension=3)
        ),
        unified_task_repository=ProcessingTaskQueryRepository(),
    )
    try:
        setup = await connection_factory()
        try:
            await KnowledgeBaseSchemaBootstrapService(
                embedding_model_name="file-build-acceptance",
                embedding_dimension=3,
            ).apply(setup)
        finally:
            await setup.close()

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                "INSERT INTO knowledge_base (kb_name) VALUES (%(name)s) RETURNING kid",
                {"name": f"file-build-{uuid4().hex}"},
            )
            knowledge_base_id = int((await cursor.fetchone())["kid"])
            await cursor.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, entry_type, is_root, name, path_ltree,
                    depth, virtual_path
                )
                VALUES (
                    %(knowledge_base_id)s, 'DIRECTORY', FALSE, 'docs',
                    'd1_docs'::ltree, 1, '/docs'
                )
                RETURNING kid
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            directory_id = int((await cursor.fetchone())["kid"])
            for index, checksum in ((1, "sha-1"), (2, "sha-2"), (3, None)):
                await cursor.execute(
                    """
                    INSERT INTO knowledge_fs_entry (
                        knowledge_base_id, parent_entry_id, entry_type, is_root,
                        name, path_ltree, depth, virtual_path, checksum,
                        file_bucket_name, file_object_key
                    )
                    VALUES (
                        %(knowledge_base_id)s, %(directory_id)s, 'FILE', FALSE,
                        %(name)s, %(path_ltree)s::ltree, 2, %(virtual_path)s,
                        %(checksum)s,
                        CASE WHEN %(ready)s THEN 'source' ELSE NULL END,
                        CASE WHEN %(ready)s THEN %(object_key)s ELSE NULL END
                    )
                    RETURNING kid
                    """,
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "directory_id": directory_id,
                        "name": f"doc-{index}.txt",
                        "path_ltree": f"d1_docs.f2_doc_{index}",
                        "virtual_path": f"/docs/doc-{index}.txt",
                        "checksum": checksum,
                        "ready": checksum is not None,
                        "object_key": f"doc-{index}.txt",
                    },
                )
            await connection.commit()
        finally:
            await connection.close()

        first = await service.accept(
            FileToMarkdownIndexRequest(knCode=str(knowledge_base_id), filePath="/docs")
        )
        assert first["scope"] == "DIRECTORY"
        assert first["candidateCount"] == 3
        assert first["eligibleCount"] == 2
        assert first["acceptedCount"] == 2
        assert first["reusedCount"] == 0
        assert first["skippedCount"] == 1

        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                SELECT extra_params
                FROM knowledge_build_batch
                WHERE batch_id = %(batch_id)s
                """,
                {"batch_id": first["batchId"]},
            )
            assert (await cursor.fetchone())["extra_params"] == {}
            await cursor.execute(
                """
                SELECT extra_params
                FROM knowledge_build_task
                WHERE batch_id = %(batch_id)s
                ORDER BY kid
                """,
                {"batch_id": first["batchId"]},
            )
            assert [row["extra_params"] for row in await cursor.fetchall()] == [{}, {}]
        finally:
            await connection.close()

        second = await service.accept(
            FileToMarkdownIndexRequest(knCode=str(knowledge_base_id), filePath="/docs")
        )
        assert second["acceptedCount"] == 0
        assert second["reusedCount"] == 2
        assert {task["status"] for task in second["tasks"]} == {"PENDING"}

        first_tasks = {task["filePathSnapshot"]: task for task in first["tasks"]}
        doc_2_task_id = int(first_tasks["/docs/doc-2.txt"]["taskId"])
        doc_2_file_id = int(first_tasks["/docs/doc-2.txt"]["fileId"])
        connection = await connection_factory()
        try:
            cursor = connection.cursor()
            await cursor.execute(
                """
                UPDATE knowledge_fs_entry
                SET markdown_bucket_name = 'markdown',
                    markdown_object_key = 'doc-2.md'
                WHERE kid = %(file_id)s
                """,
                {"file_id": doc_2_file_id},
            )
            await cursor.execute(
                """
                INSERT INTO knowledge_chunk (
                    fs_entry_id, chunk_no, start_line, end_line,
                    chunk_text, search_text
                )
                VALUES (
                    %(file_id)s, 1, 1, 1, 'doc two', to_tsvector('doc two')
                )
                RETURNING kid, search_text
                """,
                {"file_id": doc_2_file_id},
            )
            chunk = await cursor.fetchone()
            await cursor.execute(
                f"""
                INSERT INTO {embedding_table_name} (chunk_id, embedding)
                VALUES (%(chunk_id)s, '[0,0,0]'::vector)
                """,
                {"chunk_id": chunk["kid"]},
            )
            await cursor.execute(
                """
                INSERT INTO knowledge_chunk_retrieval_mv (
                    chunk_id, knowledge_base_id, fs_entry_id, full_path,
                    chunk_no, start_line, end_line, chunk_text, search_text
                )
                VALUES (
                    %(chunk_id)s, %(knowledge_base_id)s, %(file_id)s,
                    'docs/doc-2.txt', 1, 1, 1, 'doc two', %(search_text)s
                )
                """,
                {
                    "chunk_id": chunk["kid"],
                    "knowledge_base_id": knowledge_base_id,
                    "file_id": doc_2_file_id,
                    "search_text": chunk["search_text"],
                },
            )
            await cursor.execute(
                """
                UPDATE knowledge_build_task
                SET status = 'succeeded', progress = 100,
                    current_stage = 'committing', finished_at = NOW()
                WHERE kid = %(task_id)s
                """,
                {"task_id": doc_2_task_id},
            )
            await cursor.execute(
                """
                UPDATE knowledge_build_batch
                SET status = 'processing', completed_count = 1, version = 1
                WHERE batch_id = %(batch_id)s
                """,
                {"batch_id": first["batchId"]},
            )
            await connection.commit()
        finally:
            await connection.close()

        succeeded_reuse = await service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/docs/doc-2.txt"
            )
        )
        assert succeeded_reuse["acceptedCount"] == 0
        assert succeeded_reuse["reusedCount"] == 1
        assert succeeded_reuse["tasks"][0]["status"] == "SUCCEEDED"

        connection = await connection_factory()
        try:
            await connection.execute(
                f"DELETE FROM {embedding_table_name} WHERE chunk_id = %(chunk_id)s",
                {"chunk_id": chunk["kid"]},
            )
            await connection.commit()
        finally:
            await connection.close()
        incomplete_success = await service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/docs/doc-2.txt"
            )
        )
        assert incomplete_success["acceptedCount"] == 1
        assert incomplete_success["reusedCount"] == 0
        unsupported_task_id = int(incomplete_success["tasks"][0]["taskId"])
        connection = await connection_factory()
        try:
            await connection.execute(
                """
                UPDATE knowledge_build_task
                SET status = 'unsupported', progress = 100,
                    error_code = 'UNSUPPORTED_FILE_TYPE', finished_at = NOW()
                WHERE kid = %(task_id)s
                """,
                {"task_id": unsupported_task_id},
            )
            await connection.execute(
                """
                UPDATE knowledge_build_batch
                SET status = 'completed', completed_count = 1, version = 1,
                    completed_at = NOW()
                WHERE batch_id = %(batch_id)s
                """,
                {"batch_id": incomplete_success["batchId"]},
            )
            await connection.commit()
        finally:
            await connection.close()
        unsupported_reuse = await service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/docs/doc-2.txt"
            )
        )
        assert unsupported_reuse["reusedCount"] == 1
        assert unsupported_reuse["tasks"][0]["status"] == "UNSUPPORTED"
        forced = await service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id),
                filePath="/docs/doc-2.txt",
                force=True,
            )
        )
        assert forced["acceptedCount"] == 1
        assert forced["reusedCount"] == 0

        connection = await connection_factory()
        try:
            await connection.execute(
                """
                UPDATE knowledge_fs_entry
                SET checksum = 'sha-1-updated', updated_at = NOW()
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND virtual_path = '/docs/doc-1.txt'
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            await connection.commit()
        finally:
            await connection.close()

        third = await service.accept(
            FileToMarkdownIndexRequest(
                knCode=str(knowledge_base_id), filePath="/docs/doc-1.txt"
            )
        )
        assert third["scope"] == "SINGLE_FILE"
        assert third["acceptedCount"] == 1
        assert third["reusedCount"] == 0

        tasks = await service.get_processing_task_status(
            ProcessingTaskStatusRequest(
                knCode=str(knowledge_base_id),
                filePath="/docs/doc-1.txt",
                taskType=ProcessingTaskType.FILE_BUILD,
                latestOnly=False,
            )
        )
        assert tasks["total"] == 2
        assert [item["status"] for item in tasks["data"]] == ["PENDING", "SKIPPED"]
        assert tasks["data"][1]["outcomeUncertain"] is False

        unified = await service.get_unified_processing_task_status(
            ProcessingTaskStatusRequest(
                knCode=str(knowledge_base_id),
                fileId=int(tasks["data"][0]["fileId"]),
                latestOnly=True,
            )
        )
        assert unified["total"] == 1
        assert unified["data"][0]["taskType"] == "FILE_BUILD"

        batch = await service.get_processing_batch_status(
            ProcessingBatchStatusRequest(
                knCode=str(knowledge_base_id), batchId=third["batchId"]
            )
        )
        assert batch is not None
        assert batch["status"] == "PENDING"
        assert batch["totalCount"] == 1
        assert batch["pendingCount"] == 1

        connection = await connection_factory()
        try:
            await connection.execute(
                """
                INSERT INTO knowledge_fs_entry (
                    knowledge_base_id, entry_type, is_root, name, path_ltree,
                    depth, virtual_path, checksum, file_bucket_name,
                    file_object_key
                )
                VALUES (
                    %(knowledge_base_id)s, 'FILE', FALSE, 'race.txt',
                    'f1_race'::ltree, 1, '/race.txt', 'race-sha',
                    'source', 'race.txt'
                )
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            await connection.commit()
        finally:
            await connection.close()
        concurrent = await asyncio.gather(
            service.accept(
                FileToMarkdownIndexRequest(
                    knCode=str(knowledge_base_id), filePath="/race.txt"
                )
            ),
            service.accept(
                FileToMarkdownIndexRequest(
                    knCode=str(knowledge_base_id), filePath="/race.txt"
                )
            ),
        )
        assert sorted(item["acceptedCount"] for item in concurrent) == [0, 1]
        assert sorted(item["reusedCount"] for item in concurrent) == [0, 1]
    finally:
        cleanup = await connection_factory()
        try:
            await cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup.commit()
        finally:
            await cleanup.close()
