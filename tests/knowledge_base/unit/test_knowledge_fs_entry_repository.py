"""Unit tests for knowledge_fs_entry repository helpers."""

import pytest

from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)


class _RecordingCursor:
    def __init__(self, fetchone_rows):
        self.fetchone_rows = list(fetchone_rows)
        self.executed = []

    async def execute(self, sql, params=None):
        self.executed.append((sql, params or {}))

    async def fetchone(self):
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None


@pytest.mark.asyncio
async def test_move_entry_reparents_and_rewrites_subtree_paths():
    repo = KnowledgeFsEntryRepository()
    cursor = _RecordingCursor(
        [
            {
                "kid": 10,
                "knowledge_base_id": 1,
                "parent_entry_id": None,
                "path_ltree": "d1_old",
                "depth": 1,
                "virtual_path": "/old",
                "entry_type": "DIRECTORY",
            },
            {
                "kid": 20,
                "knowledge_base_id": 1,
                "parent_entry_id": None,
                "path_ltree": "d1_archive",
                "depth": 1,
                "virtual_path": "/archive",
                "entry_type": "DIRECTORY",
            },
        ]
    )

    await repo.move_entry(
        cursor,
        entry_id=10,
        new_parent_entry_id=20,
        new_name="renamed",
    )

    sql, params = cursor.executed[-1]
    normalized = " ".join(sql.split())
    assert "parent_entry_id = CASE" in normalized
    assert "depth = fs.depth + %(depth_delta)s" in normalized
    assert "virtual_path = %(new_virtual_path)s" in normalized
    assert params["entry_id"] == 10
    assert params["new_parent_entry_id"] == 20
    assert params["new_name"] == "renamed"
    assert params["current_path_ltree"] == "d1_old"
    assert params["depth_delta"] == 1
    assert params["new_virtual_path"] == "/archive/renamed"
    assert str(params["new_path_ltree"]).startswith("d1_archive.d2_")
