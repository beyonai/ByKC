"""Real OpenGauss upload concurrency; storage gates model a slow external PUT."""

import asyncio
import io
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import sql

from by_qa.config import get_settings
from by_qa.knowledge_base.api.schemas import KnowledgeItemUploadRequest
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.storage import StorageLocation, StoredObject
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)
from by_qa.knowledge_base.services.zip_batch_import_service import ZipBatchImportService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class GatedStorage:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.objects = {}
        self.completed = 0
        self.progress = asyncio.Event()

    def build_original_location(self, **kwargs):
        return StorageLocation(
            str(kwargs["knowledge_base_id"]), str(kwargs["fs_entry_id"])
        )

    async def write(self, location, content, *, content_type):
        if content == b"BLOCK":
            self.entered.set()
            await self.release.wait()
        if content == b"FAIL":
            raise RuntimeError("storage failure")
        self.objects[location] = content
        self.completed += 1
        if self.completed >= 100:
            self.progress.set()
        return StoredObject(location, content_type=content_type)

    async def delete_quietly(self, location):
        self.objects.pop(location, None)


@pytest.fixture(name="harness")
async def upload_harness():
    schema = "upload_it_" + uuid4().hex[:16]
    settings = get_settings().model_copy(
        update={"db_schema": schema, "embedding_dimension": 3}
    )
    factory = build_connection_factory(settings)
    connection = await factory()
    try:
        await KnowledgeBaseSchemaBootstrapService(
            embedding_model_name="upload-it", embedding_dimension=3
        ).apply(connection)
        rows = await connection.execute(
            "INSERT INTO knowledge_base (kb_name) VALUES ('one'), ('two') RETURNING kid"
        )
        kb, other = [row["kid"] for row in await rows.fetchall()]
        await connection.commit()
    finally:
        await connection.close()
    repo = KnowledgeFsEntryRepository()
    storage = GatedStorage()
    service = KnowledgeItemIngestionService(
        connection_factory=factory,
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=repo,
        knowledge_item_chunk_repository=None,
        retrieval_projection_repository=None,
        storage_provider=storage,
        embedding_dimension=3,
    )

    async def upload(path, content=b"content", kb_id=kb, **kwargs):
        return await service.upload_file(
            KnowledgeItemUploadRequest(
                kb_code=str(kb_id), file_path=path, file_content=content, **kwargs
            )
        )

    async def query(statement, params=None):
        connection = await factory()
        try:
            cursor = await connection.execute(statement, params)
            return await cursor.fetchall()
        finally:
            await connection.close()

    try:
        yield SimpleNamespace(
            factory=factory,
            repo=repo,
            kb=kb,
            other=other,
            storage=storage,
            service=service,
            upload=upload,
            query=query,
        )
    finally:
        storage.release.set()
        connection = await factory()
        try:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
            await connection.commit()
        finally:
            await connection.close()


async def test_concurrent_directory_creation_reuses_every_level(harness):
    h = harness

    async def create():
        connection = await h.factory()
        try:
            row = await h.repo.create_directory_entry(
                connection.cursor(),
                knowledge_base_id=h.kb,
                full_path="shared/deep/leaf",
            )
            await connection.commit()
            return row["kid"]
        finally:
            await connection.close()

    ids = await asyncio.wait_for(asyncio.gather(*(create() for _ in range(24))), 20)
    assert len(set(ids)) == 1
    assert await create() == ids[0]
    rows = await h.query(
        "SELECT virtual_path, count(*) AS n FROM knowledge_fs_entry GROUP BY virtual_path"
    )
    assert len(rows) == 3
    assert all(row["n"] == 1 for row in rows)


@pytest.mark.parametrize("path", ["same.bin", "deep/nested/same.bin"])
async def test_file_duplicate_requests_preserve_single_file(harness, path):
    h = harness
    results = await asyncio.wait_for(
        asyncio.gather(
            *(h.upload(path, str(i).encode()) for i in range(16)),
            return_exceptions=True,
        ),
        20,
    )
    assert sum(isinstance(r, dict) for r in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 15
    assert all(
        isinstance(r, KnowledgeBaseValidationError)
        and "file path already exists" in str(r)
        for r in errors
    )
    with pytest.raises(KnowledgeBaseValidationError, match="file path already exists"):
        await h.upload(path)
    assert len(h.storage.objects) == 1
    assert (
        await h.query(
            "SELECT count(*) AS n FROM knowledge_fs_entry WHERE entry_type='FILE'"
        )
    )[0]["n"] == 1


async def test_failed_upload_keeps_committed_directories_and_can_retry(harness):
    h = harness
    with pytest.raises(RuntimeError, match="storage failure"):
        await h.upload("new/deep/a.bin", b"FAIL")
    assert not h.storage.objects
    rows = await h.query("SELECT entry_type FROM knowledge_fs_entry")
    assert len(rows) == 2 and all(row["entry_type"] == "DIRECTORY" for row in rows)
    await h.upload("new/deep/a.bin", b"retry")
    assert len(h.storage.objects) == 1


@pytest.mark.parametrize("archive_count", [1, 3])
async def test_large_zip_progresses_while_one_storage_write_is_blocked(
    harness, archive_count
):
    h = harness
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("new/deep/blocked.bin", b"BLOCK")
        for i in range(512):
            archive.writestr(f"new/deep/file-{i}.bin", f"file {i}".encode())
    task = asyncio.gather(
        *(
            ZipBatchImportService(h.service).import_zip(
                kb_code=str(h.kb), target_dir=f"batch-{i}", zip_bytes=buffer.getvalue()
            )
            for i in range(archive_count)
        )
    )
    try:
        await asyncio.wait_for(h.storage.entered.wait(), 10)
        await asyncio.wait_for(
            asyncio.gather(
                h.upload("batch-0/new/deep/probe.bin", b"same directory"),
                h.upload("independent/probe.bin", b"same kb"),
                h.upload("probe.bin", b"other kb", kb_id=h.other),
            ),
            10,
        )
        await asyncio.wait_for(h.storage.progress.wait(), 20)
        assert not task.done()
        locks = await h.query(
            "SELECT mode FROM pg_locks WHERE relation = 'knowledge_fs_entry'::regclass AND mode = 'ShareRowExclusiveLock'"
        )
        assert locks == []
    finally:
        h.storage.release.set()
        result = await asyncio.wait_for(task, 60)
    for batch in result:
        assert batch.summary.total == 513
        assert batch.summary.succeeded == 513
        assert batch.summary.failed == 0
        assert not batch.post_process_errors
    rows = await h.query(
        "SELECT virtual_path, count(*) AS n FROM knowledge_fs_entry WHERE knowledge_base_id=%s GROUP BY virtual_path",
        (h.kb,),
    )
    assert all(row["n"] == 1 for row in rows)
    assert len(h.storage.objects) == 513 * archive_count + 3


async def test_directory_insert_waiter_recovers_after_creator_rollback(harness):
    h = harness
    first = await h.factory()
    second = await h.factory()
    task = None
    try:
        await h.repo.create_directory_entry(
            first.cursor(), knowledge_base_id=h.kb, full_path="rollback/deep"
        )
        task = asyncio.create_task(
            h.repo.create_directory_entry(
                second.cursor(), knowledge_base_id=h.kb, full_path="rollback/deep"
            )
        )
        await asyncio.sleep(0.1)
        assert not task.done()
        await first.rollback()
        row = await asyncio.wait_for(task, 5)
        await second.commit()
        assert row["virtual_path"] == "/rollback/deep"
    finally:
        await first.close()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.close()


async def test_file_directory_collision_and_soft_deleted_path_reuse(harness):
    h = harness
    row = await h.upload("entry", b"first")
    connection = await h.factory()
    await connection.commit()
    try:
        with pytest.raises(ValueError, match="directory path already exists"):
            await h.repo.create_directory_entry(
                connection.cursor(), knowledge_base_id=h.kb, full_path="entry"
            )
        await connection.rollback()
        await connection.execute(
            "UPDATE knowledge_fs_entry SET is_deleted=true WHERE kid=%s",
            (row["fs_entry_id"],),
        )
        await connection.commit()
        directory = await h.repo.create_directory_entry(
            connection.cursor(), knowledge_base_id=h.kb, full_path="entry"
        )
        await connection.commit()
        assert directory["kid"] != row["fs_entry_id"]
    finally:
        await connection.close()
    with pytest.raises(KnowledgeBaseValidationError, match="file path already exists"):
        await h.upload("entry", b"second")


async def test_upload_protects_ancestors_from_concurrent_mutation(harness):
    h = harness
    upload = asyncio.create_task(h.upload("parent/deep/a.bin", b"BLOCK"))
    connection = await h.factory()
    mutation = None
    try:
        await asyncio.wait_for(h.storage.entered.wait(), 10)
        mutation = asyncio.create_task(
            connection.execute(
                "UPDATE knowledge_fs_entry SET description='changed' WHERE virtual_path='/parent'"
            )
        )
        await asyncio.sleep(0.1)
        assert not mutation.done()
        h.storage.release.set()
        await asyncio.wait_for(upload, 5)
        await asyncio.wait_for(mutation, 5)
        await connection.rollback()
    finally:
        h.storage.release.set()
        await asyncio.gather(upload, return_exceptions=True)
        if mutation and not mutation.done():
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)
        await connection.close()


async def test_missing_parent_after_preparation_is_not_recreated(harness):
    h = harness
    connection = await h.factory()
    try:
        await h.repo.create_directory_entry(
            connection.cursor(), knowledge_base_id=h.kb, full_path="gone"
        )
        await connection.commit()
        await connection.execute(
            "UPDATE knowledge_fs_entry SET is_deleted=true WHERE virtual_path='/gone'"
        )
        await connection.commit()
        with pytest.raises(ValueError, match="parent directory not found"):
            await h.repo.create_file_entry(
                connection.cursor(),
                knowledge_base_id=h.kb,
                full_path="gone/a.bin",
                create_missing_parents=False,
            )
        await connection.rollback()
    finally:
        await connection.close()


async def test_upgrade_restores_missing_top_level_unique_index(harness):
    h = harness
    connection = await h.factory()
    try:
        await connection.execute(
            "DROP INDEX uq_knowledge_fs_entry_top_level_sibling_name_active"
        )
        await connection.execute(
            "DELETE FROM knowledge_schema_migration WHERE version='042_knowledge_fs_entry_upload_uniqueness.sql'"
        )
        await connection.commit()
        await KnowledgeBaseSchemaBootstrapService(
            embedding_model_name="upload-it", embedding_dimension=3
        ).apply(connection)
        rows = await connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND indexname='uq_knowledge_fs_entry_top_level_sibling_name_active'"
        )
        assert len(await rows.fetchall()) == 1
    finally:
        await connection.close()


@pytest.mark.parametrize("operation", ["rename", "move", "delete"])
async def test_tree_mutation_includes_file_committed_while_waiting(harness, operation):
    from by_qa.knowledge_base.api.schemas import (
        DeleteDirectoryRequest,
        MoveKnowledgeItemsRequest,
        UpdateDirectoryRequest,
    )
    from by_qa.knowledge_base.services.knowledge_base_service import (
        KnowledgeBaseService,
    )

    h = harness
    service = KnowledgeBaseService(
        connection_factory=h.factory,
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=h.repo,
    )
    upload = asyncio.create_task(h.upload("tree/deep/a.bin", b"BLOCK"))
    mutation = None
    try:
        await asyncio.wait_for(h.storage.entered.wait(), 10)
        if operation == "rename":
            work = service.update_directory(
                UpdateDirectoryRequest(
                    kb_code=str(h.kb), directory_path="/tree", directory_name="renamed"
                )
            )
        elif operation == "move":
            work = service.move_knowledge_items(
                MoveKnowledgeItemsRequest(
                    kb_code=str(h.kb),
                    source_path=["/tree"],
                    target_directory_path="/destination",
                )
            )
        else:
            work = service.delete_directory(
                DeleteDirectoryRequest(kb_code=str(h.kb), directory_path="/tree")
            )
        mutation = asyncio.create_task(work)
        await asyncio.sleep(0.2)
        assert not mutation.done()
        h.storage.release.set()
        row = await asyncio.wait_for(upload, 5)
        result = await asyncio.wait_for(mutation, 5)
        if operation == "move":
            assert result.summary.succeeded == 1
        files = await h.query(
            "SELECT virtual_path, is_deleted FROM knowledge_fs_entry WHERE kid=%s",
            (row["fs_entry_id"],),
        )
        if operation == "delete":
            assert files[0]["is_deleted"] is True
        else:
            prefix = "/renamed" if operation == "rename" else "/destination/tree"
            assert files[0]["virtual_path"] == prefix + "/deep/a.bin"
    finally:
        h.storage.release.set()
        await asyncio.gather(upload, return_exceptions=True)
        if mutation and not mutation.done():
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)


async def test_upgrade_refuses_historical_duplicate_directories(harness):
    h = harness
    connection = await h.factory()
    try:
        await connection.execute(
            "DROP INDEX uq_knowledge_fs_entry_top_level_sibling_name_active"
        )
        await connection.execute(
            "DELETE FROM knowledge_schema_migration WHERE version='042_knowledge_fs_entry_upload_uniqueness.sql'"
        )
        await connection.execute(
            "INSERT INTO knowledge_fs_entry (knowledge_base_id, entry_type, name, path_ltree, depth, virtual_path) VALUES (%s, 'DIRECTORY', 'duplicate', 'd1_duplicate', 1, '/duplicate'), (%s, 'DIRECTORY', 'duplicate', 'd1_duplicate', 1, '/duplicate')",
            (h.kb, h.kb),
        )
        await connection.commit()
        from psycopg.errors import UniqueViolation

        with pytest.raises(UniqueViolation):
            await KnowledgeBaseSchemaBootstrapService(
                embedding_model_name="upload-it", embedding_dimension=3
            ).apply(connection)
        rows = await h.query(
            "SELECT count(*) AS n FROM knowledge_fs_entry WHERE virtual_path='/duplicate'"
        )
        assert rows[0]["n"] == 2
    finally:
        await connection.close()
