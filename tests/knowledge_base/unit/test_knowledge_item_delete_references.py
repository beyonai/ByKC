"""Tests for file delete reference state transitions."""

# pylint: disable=unused-argument

import pytest

from by_qa.knowledge_base.api.schemas import DeleteKnowledgeItemRequest
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)


class FakeConnection:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def close(self):
        return None


class FakeCursor:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, params=None):
        self.executed.append((sql, params or {}))


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor, kb_code):  # pylint: disable=unused-argument
        return {"id": 1}


class FakeFsEntryRepository:
    def __init__(self):
        self.soft_deleted = []

    async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
        return {
            "kid": 10,
            "virtual_path": "/docs/b.md",
            "file_bucket_name": None,
            "file_object_key": None,
            "markdown_bucket_name": None,
            "markdown_object_key": None,
        }

    async def soft_delete_file_entry(self, cursor, *, knowledge_base_id, fs_entry_id):
        self.soft_deleted.append((knowledge_base_id, fs_entry_id))


class FakeReferenceRepository:
    def __init__(self):
        self.deleted_targets = []

    async def mark_targets_deleted(self, cursor, *, knowledge_base_id, targets):
        self.deleted_targets.append((knowledge_base_id, list(targets)))
        return []


class FakeStorageProvider:
    storage_path_bound_to_logical_path = False


@pytest.mark.asyncio
async def test_delete_knowledge_item_marks_inbound_references_broken_before_delete():
    connection = FakeConnection()
    fs_repo = FakeFsEntryRepository()
    reference_repo = FakeReferenceRepository()

    async def connection_factory():
        return connection

    service = KnowledgeItemIngestionService(
        connection_factory=connection_factory,
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=None,
        retrieval_projection_repository=None,
        storage_provider=FakeStorageProvider(),
        embedding_dimension=3,
        knowledge_file_reference_repository=reference_repo,
    )

    await service.delete_knowledge_item(
        DeleteKnowledgeItemRequest(kb_code="1", file_path="/docs/b.md")
    )

    assert reference_repo.deleted_targets == [(1, [(10, "/docs/b.md")])]
    assert fs_repo.soft_deleted == [(1, 10)]
    assert connection.committed == 1
