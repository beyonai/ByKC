"""Tests for knowledge item move service orchestration."""

# pylint: disable=unused-argument

import pytest

from by_qa.knowledge_base.api.schemas import MoveKnowledgeItemsRequest
from by_qa.knowledge_base.infrastructure.storage import StorageLocation
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


class FakeConnection:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def cursor(self):
        return object()

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def close(self):
        self.closed += 1


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor, kb_code):  # pylint: disable=unused-argument
        return {"id": 1, "kb_name": "kb"}


class FakeFetchCacheRepository:
    def __init__(self):
        self.delete_calls = []

    async def delete_cache_entries_for_fs_entry_ids(self, cursor, *, fs_entry_ids):
        self.delete_calls.append(list(fs_entry_ids))


class FakeRetrievalProjectionRepository:
    def __init__(self):
        self.sync_calls = []

    async def sync_full_paths_for_fs_entry_ids(
        self, cursor, *, knowledge_base_id, fs_entry_ids
    ):
        self.sync_calls.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_ids": list(fs_entry_ids),
            }
        )


class FakeStorageProvider:
    storage_path_bound_to_logical_path = True

    def __init__(self):
        self.moves = []

    def build_original_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path, mime_type
    ):
        return StorageLocation("bucket", f"/{kb_code}/raw{file_path}")

    def build_markdown_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path
    ):
        return StorageLocation("bucket", f"/{kb_code}/md{file_path}.md")

    async def move(self, source, target, *, overwrite=False):
        self.moves.append((source, target, overwrite))


class FailingMoveEntryRepositoryError(RuntimeError):
    pass


class FakeFsEntryRepository:
    def __init__(self):
        self.entries = {}
        self.created_directories = []
        self.move_calls = []
        self.location_updates = []
        self.fail_move_entry = False

    def add_entry(self, row):
        self.entries[row["virtual_path"]] = dict(row)

    async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
        return self._get_by_path(full_path, "FILE")

    async def get_directory_by_path(self, cursor, *, knowledge_base_id, full_path):
        return self._get_by_path(full_path, "DIRECTORY")

    async def create_directory_entry(
        self, cursor, *, knowledge_base_id, full_path, directory_description
    ):
        path = "/" + full_path.strip("/")
        existing = self._get_by_path(full_path, "DIRECTORY")
        if existing is not None:
            return existing
        row = {
            "kid": 1000 + len(self.created_directories),
            "name": path.rsplit("/", 1)[-1],
            "entry_type": "DIRECTORY",
            "virtual_path": path,
            "parent_entry_id": None,
            "depth": len([part for part in path.split("/") if part]),
        }
        self.created_directories.append(path)
        self.add_entry(row)
        return row

    async def get_child_entry(
        self, cursor, *, knowledge_base_id, parent_entry_id, name
    ):
        for row in self.entries.values():
            if row.get("parent_entry_id") == parent_entry_id and row["name"] == name:
                return row
        return None

    async def list_file_entries_in_subtree(
        self, cursor, *, knowledge_base_id, root_fs_entry_id
    ):
        root = next(
            row for row in self.entries.values() if row["kid"] == root_fs_entry_id
        )
        root_path = root["virtual_path"].rstrip("/")
        return [
            row
            for row in self.entries.values()
            if row["entry_type"] == "FILE"
            and (
                row["virtual_path"] == root_path
                or row["virtual_path"].startswith(root_path + "/")
            )
        ]

    async def update_file_entry_locations(
        self, cursor, *, fs_entry_id, original_location, markdown_location
    ):
        self.location_updates.append(
            (fs_entry_id, original_location, markdown_location)
        )

    async def move_entry(self, cursor, *, entry_id, new_parent_entry_id, new_name):
        if self.fail_move_entry:
            raise FailingMoveEntryRepositoryError("db move failed")
        source = next(row for row in self.entries.values() if row["kid"] == entry_id)
        old_path = source["virtual_path"]
        parent_path = "/"
        if new_parent_entry_id is not None:
            parent = next(
                row
                for row in self.entries.values()
                if row["kid"] == new_parent_entry_id
            )
            parent_path = parent["virtual_path"]
        new_path = (
            f"{parent_path.rstrip('/')}/{new_name}"
            if parent_path != "/"
            else f"/{new_name}"
        )
        self.move_calls.append((entry_id, new_parent_entry_id, new_name))
        moved_rows = [
            row
            for row in self.entries.values()
            if row["virtual_path"] == old_path
            or row["virtual_path"].startswith(old_path.rstrip("/") + "/")
        ]
        for row in moved_rows:
            row["virtual_path"] = new_path + row["virtual_path"][len(old_path) :]
            if row["kid"] == entry_id:
                row["name"] = new_name
                row["parent_entry_id"] = new_parent_entry_id
        self.entries = {row["virtual_path"]: row for row in self.entries.values()}

    def _get_by_path(self, full_path, entry_type):
        path = "/" + full_path.strip("/")
        row = self.entries.get(path)
        if row is not None and row["entry_type"] == entry_type:
            return row
        return None


def _make_service(
    connection,
    fs_repo,
    storage_provider=None,
    fetch_cache_repo=None,
    retrieval_projection_repo=None,
):
    async def connection_factory():
        return connection

    return KnowledgeBaseService(
        connection_factory=connection_factory,
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=fs_repo,
        retrieval_projection_repository=retrieval_projection_repo,
        knowledge_fetch_cache_repository=fetch_cache_repo,
        storage_provider=storage_provider,
    )


def _file(kid, path, *, parent_entry_id=None):
    return {
        "kid": kid,
        "name": path.rsplit("/", 1)[-1],
        "entry_type": "FILE",
        "virtual_path": path,
        "parent_entry_id": parent_entry_id,
        "file_bucket_name": "bucket",
        "file_object_key": f"/kb/raw{path}",
        "markdown_bucket_name": "bucket",
        "markdown_object_key": f"/kb/md{path}.md",
        "mime_type": "text/plain",
    }


@pytest.mark.asyncio
async def test_move_multiple_files_to_auto_created_directory():
    connection = FakeConnection()
    fs_repo = FakeFsEntryRepository()
    fs_repo.add_entry(_file(10, "/docs/a.md"))
    fs_repo.add_entry(_file(11, "/docs/b.md"))
    cache_repo = FakeFetchCacheRepository()
    projection_repo = FakeRetrievalProjectionRepository()
    service = _make_service(
        connection,
        fs_repo,
        fetch_cache_repo=cache_repo,
        retrieval_projection_repo=projection_repo,
    )

    result = await service.move_knowledge_items(
        MoveKnowledgeItemsRequest.model_validate(
            {
                "knCode": "kb",
                "sourcePath": ["/docs/a.md", "/docs/b.md"],
                "targetDirectoryPath": "/archive/new",
            }
        )
    )

    assert result.summary.succeeded == 2
    assert [item.target_path for item in result.data] == [
        "/archive/new/a.md",
        "/archive/new/b.md",
    ]
    assert fs_repo.created_directories == ["/archive/new"]
    assert fs_repo.move_calls == [(10, 1000, "a.md"), (11, 1000, "b.md")]
    assert projection_repo.sync_calls == [
        {"knowledge_base_id": 1, "fs_entry_ids": [10]},
        {"knowledge_base_id": 1, "fs_entry_ids": [11]},
    ]
    assert cache_repo.delete_calls == [[10], [11]]
    assert connection.committed == 2


@pytest.mark.asyncio
async def test_move_directory_syncs_retrieval_projection_for_subtree_files():
    connection = FakeConnection()
    fs_repo = FakeFsEntryRepository()
    fs_repo.add_entry(
        {
            "kid": 20,
            "name": "docs",
            "entry_type": "DIRECTORY",
            "virtual_path": "/docs",
            "parent_entry_id": None,
        }
    )
    fs_repo.add_entry(_file(21, "/docs/a.md", parent_entry_id=20))
    fs_repo.add_entry(_file(22, "/docs/nested/b.md", parent_entry_id=20))
    projection_repo = FakeRetrievalProjectionRepository()
    service = _make_service(
        connection,
        fs_repo,
        retrieval_projection_repo=projection_repo,
    )

    result = await service.move_knowledge_items(
        MoveKnowledgeItemsRequest.model_validate(
            {
                "knCode": "kb",
                "sourcePath": ["/docs"],
                "targetDirectoryPath": "/archive",
            }
        )
    )

    assert result.data[0].target_path == "/archive/docs"
    assert projection_repo.sync_calls == [
        {"knowledge_base_id": 1, "fs_entry_ids": [21, 22]}
    ]


@pytest.mark.asyncio
async def test_move_file_to_target_file_auto_creates_parent_directory():
    connection = FakeConnection()
    fs_repo = FakeFsEntryRepository()
    fs_repo.add_entry(_file(10, "/docs/a.md"))
    service = _make_service(connection, fs_repo)

    result = await service.move_knowledge_items(
        MoveKnowledgeItemsRequest.model_validate(
            {
                "knCode": "kb",
                "sourcePath": ["/docs/a.md"],
                "targetFilePath": "/archive/renamed.md",
            }
        )
    )

    assert result.data[0].target_path == "/archive/renamed.md"
    assert fs_repo.created_directories == ["/archive"]
    assert fs_repo.move_calls == [(10, 1000, "renamed.md")]


@pytest.mark.asyncio
async def test_move_directory_into_child_fails_whole_request():
    connection = FakeConnection()
    fs_repo = FakeFsEntryRepository()
    fs_repo.add_entry(
        {
            "kid": 10,
            "name": "docs",
            "entry_type": "DIRECTORY",
            "virtual_path": "/docs",
            "parent_entry_id": None,
        }
    )
    service = _make_service(connection, fs_repo)

    with pytest.raises(KnowledgeBaseValidationError):
        await service.move_knowledge_items(
            MoveKnowledgeItemsRequest.model_validate(
                {
                    "knCode": "kb",
                    "sourcePath": ["/docs"],
                    "targetDirectoryPath": "/docs/archive",
                }
            )
        )

    assert fs_repo.move_calls == []
    assert connection.committed == 0


@pytest.mark.asyncio
async def test_move_rolls_back_storage_moves_when_db_move_fails():
    connection = FakeConnection()
    fs_repo = FakeFsEntryRepository()
    fs_repo.add_entry(_file(10, "/docs/a.md"))
    fs_repo.fail_move_entry = True
    storage = FakeStorageProvider()
    service = _make_service(connection, fs_repo, storage_provider=storage)

    with pytest.raises(FailingMoveEntryRepositoryError):
        await service.move_knowledge_items(
            MoveKnowledgeItemsRequest.model_validate(
                {
                    "knCode": "kb",
                    "sourcePath": ["/docs/a.md"],
                    "targetFilePath": "/archive/a.md",
                }
            )
        )

    assert connection.rolled_back == 1
    forward_original = (
        StorageLocation("bucket", "/kb/raw/docs/a.md"),
        StorageLocation("bucket", "/kb/raw/archive/a.md"),
        False,
    )
    rollback_original = (
        StorageLocation("bucket", "/kb/raw/archive/a.md"),
        StorageLocation("bucket", "/kb/raw/docs/a.md"),
        True,
    )
    assert forward_original in storage.moves
    assert rollback_original in storage.moves
