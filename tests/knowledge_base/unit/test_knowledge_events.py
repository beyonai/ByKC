from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from by_qa.knowledge_base import events as event_module
from by_qa.knowledge_base.events import (
    BuildFileCompletedEvent,
    FileUpdatedEvent,
    FileUpdatedPayload,
    KnowledgeEventPublisherInvoker,
    ResourceEventType,
    build_file_completed_event,
    build_resource_event,
    build_semantic_terminal_events,
    parse_knowledge_event,
    serialize_knowledge_event,
)

pytestmark = pytest.mark.asyncio


class RecordingPublisher:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        if self.fail:
            raise RuntimeError("downstream unavailable")


def terminal_rows(*, batch_status="completed", task_type="ENTITY_DISCOVERY"):
    now = datetime.now(timezone.utc)
    task = {
        "kid": 12,
        "batch_id": "ed-1",
        "task_type": task_type,
        "status": "failed",
        "knowledge_base_id": 7,
        "fs_entry_id": 10,
        "file_path_snapshot": "/docs/a.md",
        "result_payload": None,
        "error_code": "MODEL_FAILED",
        "error_message": "model failed",
        "failure_kind": "MODEL",
        "outcome_uncertain": False,
        "finished_at": now,
    }
    batch = {
        "batch_id": "ed-1",
        "task_type": task_type,
        "status": batch_status,
        "knowledge_base_id": 7,
        "version": 2,
        "total_count": 2,
        "completed_count": 2,
        "completed_at": now,
    }
    return task, batch, {"succeeded": 1, "failed": 1}


async def test_semantic_terminal_events_expose_error_and_progress():
    events = build_semantic_terminal_events(*terminal_rows())

    assert [event.event_type for event in events] == [
        "semantic.discovery.file.completed",
        "semantic.discovery.batch.completed",
    ]
    assert events[0].payload.error.code == "MODEL_FAILED"
    assert events[0].payload.progress.failed_count == 1
    assert events[1].payload.progress.completed_count == 2


async def test_enrich_terminal_events_use_enrich_event_types():
    events = build_semantic_terminal_events(*terminal_rows(task_type="DOCUMENT_ENRICH"))

    assert [event.event_type for event in events] == [
        "semantic.enrich.file.completed",
        "semantic.enrich.batch.completed",
    ]


async def test_build_file_completed_event_has_strict_status_specific_payload():
    event = build_file_completed_event(
        kb_code="7",
        task_id=9901,
        file_path="docs/a.pdf",
        status="complete",
        current_step="complete",
        chunk_count=3,
        line_count=20,
    )

    assert isinstance(event, BuildFileCompletedEvent)
    assert serialize_knowledge_event(event) == {
        "eventId": event.event_id,
        "eventVersion": 1,
        "eventType": "build.file.completed",
        "knCode": "7",
        "occurredAt": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "payload": {
            "taskId": "9901",
            "status": "complete",
            "filePath": "docs/a.pdf",
            "currentStep": "complete",
            "result": {"chunkCount": 3, "lineCount": 20},
            "error": None,
        },
    }
    assert parse_knowledge_event(serialize_knowledge_event(event)) == event


async def test_build_file_completed_event_rejects_invalid_terminal_shape():
    with pytest.raises(ValueError, match="requires chunk_count"):
        build_file_completed_event(
            kb_code="7",
            task_id=9901,
            file_path="docs/a.pdf",
            status="complete",
            current_step="complete",
        )

    with pytest.raises(ValidationError):
        parse_knowledge_event(
            {
                "eventId": "evt-1",
                "eventVersion": 1,
                "eventType": "build.file.completed",
                "knCode": "7",
                "occurredAt": "2026-08-24T00:00:00Z",
                "payload": {
                    "taskId": "9901",
                    "status": "failed",
                    "filePath": "docs/a.pdf",
                    "currentStep": "markdown",
                    "result": {"chunkCount": 1, "lineCount": 1},
                    "error": None,
                },
            }
        )


async def test_batch_event_only_exists_when_batch_is_complete():
    task, batch, counts = terminal_rows(batch_status="processing")
    batch["completed_count"] = 1

    events = build_semantic_terminal_events(task, batch, counts)

    assert len(events) == 1
    assert events[0].event_type == "semantic.discovery.file.completed"


async def test_publisher_failure_does_not_escape_invoker():
    publisher = RecordingPublisher(fail=True)
    invoker = KnowledgeEventPublisherInvoker(publisher=publisher)

    await invoker.publish_all(build_semantic_terminal_events(*terminal_rows()))

    assert len(publisher.events) == 2


async def test_publisher_timeout_does_not_escape_invoker():
    class SlowPublisher:
        async def publish(self, event):
            del event
            await asyncio.sleep(0.05)

    invoker = KnowledgeEventPublisherInvoker(
        publisher=SlowPublisher(), timeout_seconds=0.001
    )

    await invoker.publish(build_semantic_terminal_events(*terminal_rows())[0])


async def test_loader_rejects_provider_without_async_publish(monkeypatch):
    monkeypatch.setattr(
        event_module,
        "import_module",
        lambda name: SimpleNamespace(build_publisher=lambda: object()),
    )

    with pytest.raises(TypeError, match="KnowledgeEventPublisher"):
        event_module.load_knowledge_event_publisher("test_provider:build_publisher")


async def test_event_publisher_is_loaded_from_single_configuration(monkeypatch):
    publisher = RecordingPublisher()
    monkeypatch.setenv(
        "BY_QA_EVENT_PUBLISHER_PROVIDER", "test_provider:build_publisher"
    )
    monkeypatch.setattr(
        event_module,
        "import_module",
        lambda name: SimpleNamespace(build_publisher=lambda: publisher),
    )

    assert event_module.load_knowledge_event_publisher() is publisher


async def test_resource_event_detaches_mutable_payload():
    result = {"success": True}
    event = build_resource_event(
        event_type=ResourceEventType.FILE_UPDATED,
        kb_code="7",
        source_path="/a.md",
        target_path="/a.md",
        result=result,
    )

    result["success"] = False

    assert isinstance(event, FileUpdatedEvent)
    assert event.payload.result.success is True
    serialized = serialize_knowledge_event(event)
    assert serialized["payload"] == {
        "resourceType": "file",
        "sourcePath": "/a.md",
        "targetPath": "/a.md",
        "result": {"success": True},
    }
    assert parse_knowledge_event(serialized) == event


async def test_payload_schema_forbids_unknown_fields_and_wrong_result_shape():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        FileUpdatedPayload.model_validate(
            {
                "resourceType": "file",
                "sourcePath": "/a.md",
                "targetPath": "/a.md",
                "result": {"success": True},
                "resourceId": "internal-id",
            }
        )

    with pytest.raises(ValidationError):
        FileUpdatedPayload.model_validate(
            {
                "resourceType": "file",
                "sourcePath": "/a.md",
                "targetPath": "/a.md",
                "result": {"succeeded": 1},
            }
        )

    with pytest.raises(ValidationError):
        parse_knowledge_event(
            {
                "eventId": "evt-1",
                "eventType": "resource.directory.created",
                "eventVersion": 1,
                "knCode": "kb-1",
                "occurredAt": "2026-08-24T00:00:00Z",
                "payload": {
                    "resourceType": "file",
                    "sourcePath": "/a.md",
                },
            }
        )
