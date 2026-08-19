from __future__ import annotations

import json

import pytest

from by_qa.knowledge_base.repositories.knowledge_semantic_processing_batch_repository import (
    KnowledgeSemanticProcessingBatchRepository,
)

pytestmark = pytest.mark.asyncio


class Cursor:
    def __init__(self, *, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []
        self.executed = []

    async def execute(self, sql, params):
        self.executed.append((sql, params))

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.all_rows


async def test_create_batch_persists_extra_params_and_zero_batch_is_completed():
    cursor = Cursor(one={"batch_id": "ed-1", "status": "completed"})
    result = await KnowledgeSemanticProcessingBatchRepository().create_batch(
        cursor,
        batch_id="ed-1",
        knowledge_base_id=7,
        task_type="ENTITY_DISCOVERY",
        scope="WHOLE_KB",
        total_count=0,
        extra_params={"requestId": "req-1"},
    )

    assert result["status"] == "completed"
    sql, params = cursor.executed[0]
    assert "knowledge_semantic_processing_batch" in sql
    assert params["status"] == "completed"
    assert json.loads(params["extra_params"]) == {"requestId": "req-1"}


async def test_advance_batch_is_bounded_and_completes_atomically():
    cursor = Cursor(one={"batch_id": "ed-1", "completed_count": 2})
    await KnowledgeSemanticProcessingBatchRepository().advance_batch(
        cursor, batch_id="ed-1"
    )

    sql, params = cursor.executed[0]
    assert "completed_count + %(completed_delta)s <= total_count" in sql
    assert "RETURNING *" in sql
    assert params == {"batch_id": "ed-1", "completed_delta": 1}


async def test_count_tasks_by_status_returns_status_map():
    cursor = Cursor(
        all_rows=[
            {"status": "succeeded", "count": 2},
            {"status": "failed", "count": 1},
        ]
    )
    result = await KnowledgeSemanticProcessingBatchRepository().count_tasks_by_status(
        cursor, batch_id="ed-1"
    )
    assert result == {"succeeded": 2, "failed": 1}
