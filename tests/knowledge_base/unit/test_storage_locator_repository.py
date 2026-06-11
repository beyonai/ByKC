"""Repository signatures should accept StorageLocation directly."""

import pytest

from by_qa.knowledge_base.infrastructure.storage import StorageLocation
from by_qa.knowledge_base.repositories.knowledge_fetch_cache_repository import (
    KnowledgeFetchCacheRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)


class FakeCursor:
    def __init__(self):
        self.executed = []
        self._next_row = None

    async def execute(self, sql, params=None):
        self.executed.append((sql, params or {}))

    async def fetchone(self):
        return self._next_row

    async def fetchall(self):
        return []


@pytest.mark.asyncio
async def test_update_file_entry_storage_accepts_storage_location():
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()
    await repo.update_file_entry_storage(
        cursor,
        fs_entry_id=71,
        file_description=None,
        original_location=StorageLocation(namespace="ns", key="k/a"),
        file_size=10,
        mime_type="text/plain",
        checksum="abc",
    )
    _, params = cursor.executed[0]
    assert params["file_bucket_name"] == "ns"
    assert params["file_object_key"] == "k/a"


@pytest.mark.asyncio
async def test_update_markdown_metadata_accepts_storage_location():
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()
    await repo.update_markdown_metadata(
        cursor,
        fs_entry_id=71,
        markdown_location=StorageLocation(namespace="md-ns", key="md/key"),
        line_count=12,
    )
    _, params = cursor.executed[0]
    assert params["markdown_bucket_name"] == "md-ns"
    assert params["markdown_object_key"] == "md/key"


@pytest.mark.asyncio
async def test_upsert_cache_entry_accepts_storage_location():
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor()
    cursor._next_row = {"kid": 99}
    await repo.upsert_cache_entry(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=71,
        full_path="a",
        source_location=StorageLocation(namespace="ns", key="k"),
        checksum="c",
        cache_file_path="/tmp/x",
        file_size=1,
        cache_ttl_seconds=60,
    )
    # The INSERT SQL should have the right params (it is the second execute call)
    _, params = cursor.executed[1]
    assert params["bucket_name"] == "ns"
    assert params["object_key"] == "k"


@pytest.mark.asyncio
async def test_delete_cache_entries_for_fs_entry_ids():
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor()
    await repo.delete_cache_entries_for_fs_entry_ids(cursor, fs_entry_ids=[1, 2, 3])
    sql, params = cursor.executed[0]
    assert "DELETE FROM knowledge_fetch_cache_index" in sql
    assert params["fs_entry_ids"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_list_file_entries_in_subtree_returns_locator_dicts():
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()
    rows = [
        {
            "kid": 11,
            "virtual_path": "/a.md",
            "file_bucket_name": "ns",
            "file_object_key": "k/a",
            "markdown_bucket_name": "md",
            "markdown_object_key": "k/a.md",
        }
    ]

    async def _fa():
        return rows

    cursor.fetchall = _fa
    result = await repo.list_file_entries_in_subtree(
        cursor, knowledge_base_id=7, root_fs_entry_id=10
    )
    assert result == rows


@pytest.mark.asyncio
async def test_update_file_entry_locations_updates_both_locators():
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()
    await repo.update_file_entry_locations(
        cursor,
        fs_entry_id=11,
        original_location=StorageLocation(namespace="ns", key="new/o"),
        markdown_location=StorageLocation(namespace="md", key="new/m"),
    )
    _, params = cursor.executed[0]
    assert params["file_bucket_name"] == "ns"
    assert params["markdown_bucket_name"] == "md"


@pytest.mark.asyncio
async def test_update_file_entry_locations_partial():
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()
    await repo.update_file_entry_locations(
        cursor,
        fs_entry_id=11,
        original_location=StorageLocation(namespace="ns", key="o"),
        markdown_location=None,
    )
    sql, _ = cursor.executed[0]
    assert "markdown_bucket_name" not in sql
