"""Service-level tests for storage_path_bound_to_logical_path=True providers."""

# pylint: disable=unused-argument

from by_qa.knowledge_base.infrastructure.storage import StorageLocation, StoredObject
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


class FakePathBoundProvider:
    provider_name = "fake-bound"
    storage_path_bound_to_logical_path = True

    def __init__(self):
        self.payloads = {}
        self.deleted_quietly = []
        self.moves = []

    async def ensure_ready(self):
        pass

    def build_original_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path, mime_type
    ):
        return StorageLocation(namespace="bound", key=f"/{kb_code}/raw{file_path}")

    def build_markdown_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path
    ):
        return StorageLocation(namespace="bound", key=f"/{kb_code}/md{file_path}.md")

    async def write(self, location, content, *, content_type):
        self.payloads[(location.namespace, location.key)] = content
        return StoredObject(
            location=location, size=len(content), content_type=content_type
        )

    async def read(self, location):
        return self.payloads[(location.namespace, location.key)]

    async def delete(self, location):
        self.payloads.pop((location.namespace, location.key), None)

    async def delete_quietly(self, location):
        self.deleted_quietly.append(location)
        self.payloads.pop((location.namespace, location.key), None)

    async def move(self, source, target, *, overwrite=False):
        self.moves.append((source, target))
        if (source.namespace, source.key) in self.payloads:
            self.payloads[(target.namespace, target.key)] = self.payloads.pop(
                (source.namespace, source.key)
            )


class FakePathUnboundProvider:
    provider_name = "fake-unbound"
    storage_path_bound_to_logical_path = False

    def __init__(self):
        self.payloads = {}
        self.deleted_quietly = []
        self.moves = []

    async def ensure_ready(self):
        pass

    def build_original_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path, mime_type
    ):
        return StorageLocation(namespace="unbound", key=f"/{kb_code}/raw{file_path}")

    def build_markdown_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path
    ):
        return StorageLocation(namespace="unbound", key=f"/{kb_code}/md{file_path}.md")

    async def write(self, location, content, *, content_type):
        self.payloads[(location.namespace, location.key)] = content
        return StoredObject(
            location=location, size=len(content), content_type=content_type
        )

    async def read(self, location):
        return self.payloads[(location.namespace, location.key)]

    async def delete(self, location):
        self.payloads.pop((location.namespace, location.key), None)

    async def delete_quietly(self, location):
        self.deleted_quietly.append(location)
        self.payloads.pop((location.namespace, location.key), None)

    async def move(self, source, target, *, overwrite=False):
        self.moves.append((source, target))
        if (source.namespace, source.key) in self.payloads:
            self.payloads[(target.namespace, target.key)] = self.payloads.pop(
                (source.namespace, source.key)
            )


class FakeCursor:
    def __init__(self):
        self.executed = []
        self._fetchone_results = []
        self._fetchall_results = []

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchone(self):
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    async def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []


class FakeConnection:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def cursor(self):
        return self._cursor

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def close(self):
        self.closed += 1


class FakeKnowledgeBaseRepository:
    def __init__(self, kb_row=None):
        self.kb_row = kb_row or {"id": 1, "kb_name": "test-kb"}

    async def get_by_code(self, cursor, kb_code):
        return self.kb_row


class FakeFsEntryRepository:
    def __init__(self):
        self.soft_delete_subtree_called = False
        self.list_file_entries_called = False
        self._directory_row = None
        self._subtree_ids = []
        self._file_locator_rows = []
        self._update_location_calls = []

    async def get_directory_by_path(self, cursor, *, knowledge_base_id, full_path):
        return self._directory_row

    async def list_subtree_entry_ids(
        self, cursor, *, knowledge_base_id, root_fs_entry_id
    ):
        return list(self._subtree_ids)

    async def soft_delete_subtree(self, cursor, *, knowledge_base_id, root_fs_entry_id):
        self.soft_delete_subtree_called = True

    async def list_file_entries_in_subtree(
        self, cursor, *, knowledge_base_id, root_fs_entry_id
    ):
        self.list_file_entries_called = True
        return list(self._file_locator_rows)

    async def update_file_entry_locations(
        self, cursor, *, fs_entry_id, original_location, markdown_location
    ):
        self._update_location_calls.append(
            (fs_entry_id, original_location, markdown_location)
        )

    async def rename_entry(self, cursor, *, entry_id, new_name):
        pass

    async def get_child_entry(
        self, cursor, *, knowledge_base_id, parent_entry_id, name
    ):
        return None


class FakeFetchCacheRepository:
    def __init__(self):
        self.delete_calls = []

    async def delete_cache_entries_for_fs_entry_ids(self, cursor, *, fs_entry_ids):
        self.delete_calls.append(list(fs_entry_ids))


def _make_service(connection, kb_repo, fs_repo, fetch_cache_repo, storage_provider):
    async def connection_factory():
        return connection

    return KnowledgeBaseService(
        connection_factory=connection_factory,
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_fetch_cache_repository=fetch_cache_repo,
        storage_provider=storage_provider,
    )


# --- Task 4.2: delete_directory tests ---


async def test_delete_directory_clears_storage_when_path_bound():
    """Provider with path_bound=True: delete_directory calls delete_quietly for subtree files."""
    from by_qa.knowledge_base.api.schemas import DeleteDirectoryRequest

    cursor = FakeCursor()
    connection = FakeConnection()
    connection._cursor = cursor

    kb_repo = FakeKnowledgeBaseRepository()
    prov = FakePathBoundProvider()
    fs_repo = FakeFsEntryRepository()
    fs_repo._directory_row = {"kid": 10, "entry_type": "DIRECTORY"}
    fs_repo._subtree_ids = [10, 11, 12]
    fs_repo._file_locator_rows = [
        {
            "kid": 11,
            "virtual_path": "/dir/file1.txt",
            "file_bucket_name": "bound",
            "file_object_key": "/test-kb/raw/dir/file1.txt",
            "markdown_bucket_name": "bound",
            "markdown_object_key": "/test-kb/md/dir/file1.txt.md",
        },
        {
            "kid": 12,
            "virtual_path": "/dir/file2.txt",
            "file_bucket_name": "bound",
            "file_object_key": "/test-kb/raw/dir/file2.txt",
            "markdown_bucket_name": "bound",
            "markdown_object_key": "/test-kb/md/dir/file2.txt.md",
        },
    ]
    fetch_cache_repo = FakeFetchCacheRepository()

    service = _make_service(connection, kb_repo, fs_repo, fetch_cache_repo, prov)

    # Put payloads so delete_quietly can pop them
    for row in fs_repo._file_locator_rows:
        prov.payloads[("bound", row["file_object_key"])] = b"orig"
        prov.payloads[("bound", row["markdown_object_key"])] = b"md"

    await service.delete_directory(
        DeleteDirectoryRequest(kb_code="test-kb", directory_path="/dir")
    )

    assert connection.committed == 1
    assert len(prov.deleted_quietly) == 4  # 2 files * 2 locations each
    assert fs_repo.list_file_entries_called is True
    assert fetch_cache_repo.delete_calls == [[10, 11, 12]]


async def test_delete_directory_does_not_touch_storage_when_path_not_bound():
    """Provider with path_bound=False: delete_directory does not call delete_quietly."""
    from by_qa.knowledge_base.api.schemas import DeleteDirectoryRequest

    cursor = FakeCursor()
    connection = FakeConnection()
    connection._cursor = cursor

    kb_repo = FakeKnowledgeBaseRepository()
    prov = FakePathUnboundProvider()
    fs_repo = FakeFsEntryRepository()
    fs_repo._directory_row = {"kid": 10, "entry_type": "DIRECTORY"}
    fs_repo._subtree_ids = [10, 11]
    fs_repo._file_locator_rows = [
        {
            "kid": 11,
            "virtual_path": "/dir/file1.txt",
            "file_bucket_name": "unbound",
            "file_object_key": "/test-kb/raw/dir/file1.txt",
            "markdown_bucket_name": None,
            "markdown_object_key": None,
        },
    ]
    fetch_cache_repo = FakeFetchCacheRepository()

    service = _make_service(connection, kb_repo, fs_repo, fetch_cache_repo, prov)

    await service.delete_directory(
        DeleteDirectoryRequest(kb_code="test-kb", directory_path="/dir")
    )

    assert connection.committed == 1
    assert len(prov.deleted_quietly) == 0
    assert fs_repo.list_file_entries_called is False


# --- Task 4.3: update_directory tests ---


async def test_update_directory_moves_storage_and_updates_locators_when_path_bound():
    """Provider with path_bound=True: update_directory moves objects and syncs locators."""
    from by_qa.knowledge_base.api.schemas import UpdateDirectoryRequest

    cursor = FakeCursor()
    connection = FakeConnection()
    connection._cursor = cursor

    kb_repo = FakeKnowledgeBaseRepository()
    prov = FakePathBoundProvider()
    fs_repo = FakeFsEntryRepository()
    fs_repo._directory_row = {
        "kid": 10,
        "entry_type": "DIRECTORY",
        "parent_entry_id": None,
    }
    fs_repo._file_locator_rows = [
        {
            "kid": 11,
            "virtual_path": "/olddir/file1.txt",
            "file_bucket_name": "bound",
            "file_object_key": "/test-kb/raw/olddir/file1.txt",
            "markdown_bucket_name": "bound",
            "markdown_object_key": "/test-kb/md/olddir/file1.txt.md",
            "mime_type": "text/plain",
        },
        {
            "kid": 12,
            "virtual_path": "/olddir/sub/file2.txt",
            "file_bucket_name": "bound",
            "file_object_key": "/test-kb/raw/olddir/sub/file2.txt",
            "markdown_bucket_name": "bound",
            "markdown_object_key": "/test-kb/md/olddir/sub/file2.txt.md",
            "mime_type": "application/octet-stream",
        },
    ]
    fetch_cache_repo = FakeFetchCacheRepository()

    # Put payloads so move can transfer them
    for row in fs_repo._file_locator_rows:
        prov.payloads[("bound", row["file_object_key"])] = b"orig"
        prov.payloads[("bound", row["markdown_object_key"])] = b"md"

    service = _make_service(connection, kb_repo, fs_repo, fetch_cache_repo, prov)

    await service.update_directory(
        UpdateDirectoryRequest(
            kb_code="test-kb", directory_path="/olddir", directory_name="newdir"
        )
    )

    assert connection.committed == 1
    # 2 files * 2 locations each = 4 moves
    assert len(prov.moves) == 4
    # Verify moves: old -> new paths
    assert (
        StorageLocation("bound", "/test-kb/raw/olddir/file1.txt"),
        StorageLocation("bound", "/test-kb/raw/newdir/file1.txt"),
    ) in prov.moves
    assert (
        StorageLocation("bound", "/test-kb/md/olddir/file1.txt.md"),
        StorageLocation("bound", "/test-kb/md/newdir/file1.txt.md"),
    ) in prov.moves
    assert (
        StorageLocation("bound", "/test-kb/raw/olddir/sub/file2.txt"),
        StorageLocation("bound", "/test-kb/raw/newdir/sub/file2.txt"),
    ) in prov.moves
    assert (
        StorageLocation("bound", "/test-kb/md/olddir/sub/file2.txt.md"),
        StorageLocation("bound", "/test-kb/md/newdir/sub/file2.txt.md"),
    ) in prov.moves
    # Locator updates: 2 entries updated
    assert len(fs_repo._update_location_calls) == 2
    # Fetch cache invalidated for both fs_entry_ids
    assert fetch_cache_repo.delete_calls == [[11, 12]]


async def test_update_directory_does_not_move_storage_when_path_not_bound():
    """Provider with path_bound=False: update_directory does not move storage."""
    from by_qa.knowledge_base.api.schemas import UpdateDirectoryRequest

    cursor = FakeCursor()
    connection = FakeConnection()
    connection._cursor = cursor

    kb_repo = FakeKnowledgeBaseRepository()
    prov = FakePathUnboundProvider()
    fs_repo = FakeFsEntryRepository()
    fs_repo._directory_row = {
        "kid": 10,
        "entry_type": "DIRECTORY",
        "parent_entry_id": None,
    }
    fs_repo._file_locator_rows = [
        {
            "kid": 11,
            "virtual_path": "/olddir/file1.txt",
            "file_bucket_name": "unbound",
            "file_object_key": "/test-kb/raw/olddir/file1.txt",
            "markdown_bucket_name": None,
            "markdown_object_key": None,
            "mime_type": "text/plain",
        },
    ]
    fetch_cache_repo = FakeFetchCacheRepository()

    service = _make_service(connection, kb_repo, fs_repo, fetch_cache_repo, prov)

    await service.update_directory(
        UpdateDirectoryRequest(
            kb_code="test-kb", directory_path="/olddir", directory_name="newdir"
        )
    )

    assert connection.committed == 1
    assert len(prov.moves) == 0
    assert len(fs_repo._update_location_calls) == 0
    assert fetch_cache_repo.delete_calls == []


# --- Task 4.4: delete_knowledge_item cache invalidation ---


async def test_delete_knowledge_item_invalidates_cache_via_repository():
    """delete_knowledge_item calls delete_cache_entries_for_fs_entry_ids via repository."""
    from by_qa.knowledge_base.api.schemas import DeleteKnowledgeItemRequest
    from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
        KnowledgeItemIngestionService,
    )

    cursor = FakeCursor()
    connection = FakeConnection()
    connection._cursor = cursor

    kb_repo = FakeKnowledgeBaseRepository()
    prov = FakePathBoundProvider()

    class FakeFsEntryRepoForDeleteItem:
        async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
            return {
                "kid": 11,
                "file_bucket_name": "bound",
                "file_object_key": "/test-kb/raw/dir/file1.txt",
                "markdown_bucket_name": "bound",
                "markdown_object_key": "/test-kb/md/dir/file1.txt.md",
            }

        async def soft_delete_file_entry(
            self, cursor, *, knowledge_base_id, fs_entry_id
        ):
            pass

    fetch_cache_repo = FakeFetchCacheRepository()

    async def connection_factory():
        return connection

    service = KnowledgeItemIngestionService(
        connection_factory=connection_factory,
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=FakeFsEntryRepoForDeleteItem(),
        knowledge_item_chunk_repository=None,
        retrieval_projection_repository=None,
        storage_provider=prov,
        embedding_dimension=1536,
        knowledge_fetch_cache_repository=fetch_cache_repo,
    )

    await service.delete_knowledge_item(
        DeleteKnowledgeItemRequest(kb_code="test-kb", file_path="/dir/file1.txt")
    )

    assert connection.committed == 1
    # Cache invalidation via repository, not inline SQL
    assert fetch_cache_repo.delete_calls == [[11]]
    # Path-bound delete_quietly for original and markdown
    assert len(prov.deleted_quietly) == 2
