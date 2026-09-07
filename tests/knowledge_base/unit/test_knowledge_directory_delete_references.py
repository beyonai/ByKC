"""Tests for directory delete reference state transitions."""

# pylint: disable=unused-argument

import pytest

from by_qa.knowledge_base.api.schemas import DeleteDirectoryRequest
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


class FakeCursor:
    async def execute(self, sql, params=None):
        return None


class FakeConnection:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return FakeCursor()

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def close(self):
        return None


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor, kb_code):  # pylint: disable=unused-argument
        return {"id": 1}


class FakeFsEntryRepository:
    def __init__(self):
        self.soft_deleted = []
        self.file_rows = [
            {"kid": 11, "virtual_path": "/docs/b.md"},
            {"kid": 12, "virtual_path": "/docs/sub/c.md"},
        ]

    async def get_directory_by_path(self, cursor, *, knowledge_base_id, full_path):
        return {"kid": 10, "entry_type": "DIRECTORY", "virtual_path": "/docs"}

    async def list_subtree_entry_ids(
        self, cursor, *, knowledge_base_id, root_fs_entry_id
    ):
        return [10, 11, 12]

    async def list_file_entries_in_subtree(
        self, cursor, *, knowledge_base_id, root_fs_entry_id
    ):
        return list(self.file_rows)

    async def soft_delete_subtree(self, cursor, *, knowledge_base_id, root_fs_entry_id):
        self.soft_deleted.append((knowledge_base_id, root_fs_entry_id))


class FakeReferenceRepository:
    def __init__(self):
        self.calls = []
        self.deleted_sources = []
        self.deleted_targets = []

    async def delete_outgoing_for_source_fs_entry_ids(
        self, cursor, *, knowledge_base_id, source_fs_entry_ids
    ):
        self.calls.append("delete_outgoing")
        self.deleted_sources.append((knowledge_base_id, list(source_fs_entry_ids)))
        return []

    async def mark_targets_deleted(self, cursor, *, knowledge_base_id, targets):
        self.calls.append("mark_targets_deleted")
        self.deleted_targets.append((knowledge_base_id, list(targets)))
        return []


class FakeBuildMutationService:
    def __init__(self, connection):
        self.connection = connection
        self.terminated = []
        self.published = []

    async def terminate_active(self, cursor, **kwargs):
        self.terminated.append(kwargs)
        return ([{"kid": 81}], ["fb-1"])

    async def publish(self, tasks, completed_batch_ids):
        assert self.connection.committed == 1
        self.published.append((tasks, completed_batch_ids))


@pytest.mark.asyncio
async def test_delete_directory_removes_outgoing_and_breaks_inbound_references():
    connection = FakeConnection()
    fs_repo = FakeFsEntryRepository()
    reference_repo = FakeReferenceRepository()
    build_mutation = FakeBuildMutationService(connection)

    async def connection_factory():
        return connection

    service = KnowledgeBaseService(
        connection_factory=connection_factory,
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=fs_repo,
        knowledge_file_reference_repository=reference_repo,
        file_build_mutation_service=build_mutation,
    )

    await service.delete_directory(
        DeleteDirectoryRequest(kb_code="1", directory_path="/docs")
    )

    assert reference_repo.deleted_sources == [(1, [10, 11, 12])]
    assert reference_repo.deleted_targets == [
        (1, [(11, "/docs/b.md"), (12, "/docs/sub/c.md")])
    ]
    assert reference_repo.calls == ["delete_outgoing", "mark_targets_deleted"]
    assert fs_repo.soft_deleted == [(1, 10)]
    assert connection.committed == 1
    assert build_mutation.terminated == [
        {
            "knowledge_base_id": 1,
            "fs_entry_ids": [10, 11, 12],
            "error_code": "SOURCE_DELETED",
            "error_message": "Source directory was deleted",
        }
    ]
    assert build_mutation.published == [([{"kid": 81}], ["fb-1"])]
