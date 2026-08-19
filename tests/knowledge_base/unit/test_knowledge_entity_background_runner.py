from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from by_qa.knowledge_base.services import knowledge_entity_background_runner
from by_qa.knowledge_base.services.knowledge_entity_background_runner import (
    KnowledgeEntityBackgroundRunner,
)
from by_qa.knowledge_base.services.knowledge_entity_callback import (
    KnowledgeEntityCallbackInvoker,
)

pytestmark = pytest.mark.asyncio


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def close(self):
        pass


class Connections:
    def __init__(self):
        self.values = []

    async def __call__(self):
        connection = Connection()
        self.values.append(connection)
        return connection


class Tasks:
    def __init__(self, rows):
        self.rows = rows
        self.claim_calls = 0
        self.finished = []
        self.expired = []

    async def claim_next_task(self, cursor, **claim):
        del cursor
        self.claim_calls += 1
        row = next((item for item in self.rows if item["status"] == "pending"), None)
        if row is None:
            return None
        row.update(
            status="running",
            worker_id=claim["worker_id"],
            lease_token=claim["lease_token"],
        )
        return dict(row)

    async def refresh_lease(self, cursor, **values):
        del cursor, values
        return True

    async def finish_claimed_task(self, cursor, **values):
        del cursor
        row = next(item for item in self.rows if item["kid"] == values["task_id"])
        if row.get("lease_token") != values["lease_token"]:
            return None
        row.update(
            status=values["status"],
            current_stage=values["current_stage"],
            progress=100,
            result_payload=values["result_payload"],
            error_code=values["error_code"],
            error_message=values["error_message"],
            failure_kind=values["failure_kind"],
            outcome_uncertain=values["outcome_uncertain"],
            finished_at=datetime.now(timezone.utc),
        )
        self.finished.append(row["kid"])
        return dict(row)

    async def lock_next_expired_task(self, cursor):
        del cursor
        return self.expired.pop(0) if self.expired else None

    async def fail_locked_expired_task(self, cursor, *, task_id):
        del cursor
        row = next(item for item in self.rows if item["kid"] == task_id)
        row.update(
            status="failed",
            progress=100,
            error_code="WORKER_LOST",
            error_message="worker lease expired",
            failure_kind="INFRASTRUCTURE",
            outcome_uncertain=True,
            finished_at=datetime.now(timezone.utc),
        )
        return dict(row)


class Batches:
    def __init__(self, rows, tasks):
        self.rows = rows
        self.tasks = tasks

    async def advance_batch(self, cursor, *, batch_id):
        del cursor
        batch = self.rows[batch_id]
        batch["completed_count"] += 1
        batch["version"] += 1
        if batch["completed_count"] == batch["total_count"]:
            batch["status"] = "completed"
            batch["completed_at"] = datetime.now(timezone.utc)
        return dict(batch)

    async def count_tasks_by_status(self, cursor, *, batch_id):
        del cursor
        counts = {}
        for row in self.tasks.rows:
            if row["batch_id"] == batch_id:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts


class Worker:
    def __init__(self, failing_ids=()):
        self.failing_ids = set(failing_ids)
        self.calls = []

    async def run_task(self, context):
        self.calls.append(context.task_id)
        if context.task_id in self.failing_ids:
            raise RuntimeError("model failed")
        return {"resultPayload": {"createdCount": 1}, "indexVersion": "ac/1"}


class BlockingWorker:
    def __init__(self):
        self.calls = 0
        self.cancelled = False

    async def run_task(self, context):
        del context
        self.calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


class Callback:
    def __init__(self):
        self.files = []
        self.batches = []

    async def on_file_completed(self, event):
        self.files.append(event)

    async def on_batch_completed(self, event):
        self.batches.append(event)


def task(task_id):
    return {
        "kid": task_id,
        "batch_id": "ed-1",
        "knowledge_base_id": 7,
        "fs_entry_id": task_id + 100,
        "file_path_snapshot": f"/docs/{task_id}.md",
        "task_type": "ENTITY_DISCOVERY",
        "status": "pending",
        "input_fingerprint": f"fp-{task_id}",
        "input_checksum": f"sha-{task_id}",
        "request_params": {},
        "extra_params": {"requestId": "req-1"},
    }


def make_runner(rows, *, failing_ids=()):
    connections = Connections()
    tasks = Tasks(rows)
    batches = Batches(
        {
            "ed-1": {
                "batch_id": "ed-1",
                "knowledge_base_id": 7,
                "task_type": "ENTITY_DISCOVERY",
                "status": "processing",
                "total_count": len(rows),
                "completed_count": 0,
                "version": 0,
                "extra_params": {"requestId": "req-1"},
                "completed_at": None,
            }
        },
        tasks,
    )
    callback = Callback()
    worker = Worker(failing_ids)
    runner = KnowledgeEntityBackgroundRunner(
        connection_factory=connections,
        task_repository=tasks,
        batch_repository=batches,
        worker=worker,
        callback_invoker=KnowledgeEntityCallbackInvoker(callback),
        worker_id="worker-1",
        concurrency=2,
        lease_seconds=10,
        heartbeat_seconds=1,
    )
    return runner, tasks, batches, worker, callback


async def test_claim_cycle_processes_files_independently_without_business_retry():
    rows = [task(1), task(2)]
    runner, tasks, batches, worker, callback = make_runner(rows, failing_ids={2})

    assert await runner.run_claim_cycle() == 2
    await asyncio.gather(*tuple(runner._active_tasks))

    assert worker.calls == [1, 2]
    assert tasks.claim_calls == 2
    assert [row["status"] for row in rows] == ["succeeded", "failed"]
    assert batches.rows["ed-1"]["status"] == "completed"
    assert len(callback.files) == 2
    assert len(callback.batches) == 1


async def test_reaper_marks_expired_running_task_failed_without_requeue():
    row = task(3)
    row.update(status="running", lease_token="lost-lease")
    runner, tasks, batches, worker, callback = make_runner([row])
    tasks.expired.append(dict(row))

    assert await runner.run_reaper_cycle() == 1

    assert worker.calls == []
    assert row["status"] == "failed"
    assert row["error_code"] == "WORKER_LOST"
    assert row["outcome_uncertain"] is True
    assert batches.rows["ed-1"]["status"] == "completed"
    assert len(callback.files) == 1
    assert len(callback.batches) == 1


async def test_deleted_source_is_terminal_input_failure_not_stuck_running():
    row = task(4)
    row["fs_entry_id"] = None
    runner, _, batches, worker, callback = make_runner([row])

    assert await runner.run_claim_cycle() == 1
    await asyncio.gather(*tuple(runner._active_tasks))

    assert worker.calls == []
    assert row["status"] == "failed"
    assert row["error_code"] == "TASK_INPUT_UNAVAILABLE"
    assert batches.rows["ed-1"]["status"] == "completed"
    assert len(callback.files) == 1


async def test_task_timeout_is_terminal_failure_without_retry():
    row = task(5)
    runner, _, batches, _, callback = make_runner([row])
    worker = BlockingWorker()
    runner.worker = worker
    runner.task_timeout_seconds = 0.01

    assert await runner.run_claim_cycle() == 1
    await asyncio.gather(*tuple(runner._active_tasks))

    assert worker.cancelled is True
    assert worker.calls == 1
    assert row["status"] == "failed"
    assert row["error_code"] == "TASK_TIMEOUT"
    assert row["failure_kind"] == "INFRASTRUCTURE"
    assert row["outcome_uncertain"] is True
    assert batches.rows["ed-1"]["status"] == "completed"
    assert len(callback.files) == 1
    assert len(callback.batches) == 1


async def test_runner_periodically_logs_worker_liveness(monkeypatch):
    runner, *_ = make_runner([])
    runner.status_log_seconds = 0.01
    info = Mock()
    monkeypatch.setattr(knowledge_entity_background_runner.logger, "info", info)

    await runner.start()
    await asyncio.sleep(0.025)
    await runner.stop()

    status_calls = [
        call
        for call in info.call_args_list
        if call.args and call.args[0].startswith("knowledge_entity worker status:")
    ]
    assert status_calls
    assert status_calls[0].args[1:] == (
        "worker-1",
        2,
        0,
        2,
        "-",
        True,
        True,
    )
