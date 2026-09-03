"""Tests for File Build batch aggregation persistence."""

import pytest

from by_qa.knowledge_base.repositories.knowledge_build_batch_repository import (
    KnowledgeBuildBatchRepository,
)


class FakeCursor:
    def __init__(self, *, fetchone_results=None, fetchall_results=None):
        self.executed: list[tuple[str, dict | None]] = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    async def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []


async def test_empty_or_reused_batch_is_immediately_completed():
    repo = KnowledgeBuildBatchRepository()
    cursor = FakeCursor(fetchone_results=[{"batch_id": "fb-1"}])

    row = await repo.create_batch(
        cursor,
        batch_id="fb-1",
        knowledge_base_id=7,
        scope="DIRECTORY",
        target_path_snapshot="/docs",
        candidate_count=2,
        eligible_count=2,
        accepted_count=0,
        reused_count=2,
        acceptance_skipped_count=0,
    )

    assert row == {"batch_id": "fb-1"}
    _, params = cursor.executed[0]
    assert params["status"] == "completed"
    assert params["completed"] is True


async def test_non_empty_batch_starts_pending_and_advances_atomically():
    repo = KnowledgeBuildBatchRepository()
    cursor = FakeCursor(
        fetchone_results=[{"status": "pending"}, {"status": "completed"}]
    )

    await repo.create_batch(
        cursor,
        batch_id="fb-2",
        knowledge_base_id=7,
        scope="SINGLE_FILE",
        target_path_snapshot="/a.pdf",
        candidate_count=1,
        eligible_count=1,
        accepted_count=1,
        reused_count=0,
        acceptance_skipped_count=0,
    )
    row = await repo.advance_batch(cursor, batch_id="fb-2")

    assert row == {"status": "completed"}
    _, create_params = cursor.executed[0]
    advance_sql, advance_params = cursor.executed[1]
    assert create_params["status"] == "pending"
    assert "completed_count + %(completed_delta)s" in advance_sql
    assert "accepted_count" in advance_sql
    assert advance_params == {"batch_id": "fb-2", "completed_delta": 1}


def test_batch_repository_rejects_inconsistent_counts():
    repo = KnowledgeBuildBatchRepository()

    with pytest.raises(ValueError, match="candidate_count invariant"):
        repo._validate_counts(
            candidate_count=3,
            eligible_count=1,
            accepted_count=1,
            reused_count=0,
            acceptance_skipped_count=1,
        )
