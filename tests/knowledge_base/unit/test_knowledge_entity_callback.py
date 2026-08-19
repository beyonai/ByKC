from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from by_qa.knowledge_base.services import knowledge_entity_callback as callback_module
from by_qa.knowledge_base.services.knowledge_entity_callback import (
    KnowledgeEntityCallbackInvoker,
    invoke_terminal_callbacks,
)

pytestmark = pytest.mark.asyncio


class Callback:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.files = []
        self.batches = []

    async def on_file_completed(self, event):
        self.files.append(event)
        if self.fail:
            raise RuntimeError("downstream unavailable")

    async def on_batch_completed(self, event):
        self.batches.append(event)
        if self.fail:
            raise RuntimeError("downstream unavailable")


def terminal_rows(*, batch_status="completed"):
    now = datetime.now(timezone.utc)
    task = {
        "kid": 12,
        "batch_id": "ed-1",
        "task_type": "ENTITY_DISCOVERY",
        "status": "failed",
        "knowledge_base_id": 7,
        "fs_entry_id": 10,
        "file_path_snapshot": "/docs/a.md",
        "result_payload": None,
        "error_code": "MODEL_FAILED",
        "error_message": "model failed",
        "failure_kind": "MODEL",
        "outcome_uncertain": False,
        "extra_params": '{"requestId":"req-1"}',
        "finished_at": now,
    }
    batch = {
        "batch_id": "ed-1",
        "task_type": "ENTITY_DISCOVERY",
        "status": batch_status,
        "knowledge_base_id": 7,
        "version": 2,
        "total_count": 2,
        "completed_count": 2,
        "extra_params": {"requestId": "req-1"},
        "completed_at": now,
    }
    return task, batch, {"succeeded": 1, "failed": 1}


async def test_terminal_callback_exposes_file_error_batch_progress_and_extra_params():
    callback = Callback()
    await invoke_terminal_callbacks(
        KnowledgeEntityCallbackInvoker(callback), *terminal_rows()
    )

    assert len(callback.files) == 1
    assert callback.files[0].error.code == "MODEL_FAILED"
    assert callback.files[0].progress.failed_count == 1
    assert callback.files[0].extra_params == {"requestId": "req-1"}
    assert len(callback.batches) == 1
    assert callback.batches[0].progress.completed_count == 2


async def test_callback_failure_does_not_change_or_raise_from_terminal_delivery():
    callback = Callback(fail=True)

    await invoke_terminal_callbacks(
        KnowledgeEntityCallbackInvoker(callback), *terminal_rows()
    )

    assert len(callback.files) == 1
    assert len(callback.batches) == 1


async def test_batch_callback_only_fires_when_batch_is_complete():
    callback = Callback()
    task, batch, counts = terminal_rows(batch_status="processing")
    batch["completed_count"] = 1

    await invoke_terminal_callbacks(
        KnowledgeEntityCallbackInvoker(callback), task, batch, counts
    )

    assert len(callback.files) == 1
    assert callback.batches == []


async def test_callback_provider_is_loaded_from_service_configuration(monkeypatch):
    callback = Callback()
    monkeypatch.setenv("KNOWLEDGE_ENTITY_CALLBACK_PROVIDER", "test_provider:build")
    monkeypatch.setattr(
        callback_module,
        "import_module",
        lambda name: SimpleNamespace(build=lambda: callback),
    )

    assert callback_module.load_knowledge_entity_processing_callback() is callback
