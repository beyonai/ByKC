"""Unit tests for transactional single-document replacement."""

from __future__ import annotations

import hashlib

import pytest

from by_qa.knowledge_base.api.schemas import DocumentUpdateRequest
from by_qa.knowledge_base.infrastructure.storage import StorageLocation, StoredObject
from by_qa.knowledge_base.services.document_update_service import (
    DocumentUpdateService,
    GeneratedOutgoingAssertion,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError

pytestmark = pytest.mark.asyncio


class Connection:
    def __init__(self, calls, fail_commit=False, fail_rollback=False):
        self.calls = calls
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.cursor_obj = object()
        self.rolled_back = False

    def cursor(self):
        return self.cursor_obj

    async def commit(self):
        self.calls.append(("commit", {}))
        if self.fail_commit:
            raise RuntimeError("database unavailable")

    async def rollback(self):
        self.rolled_back = True
        self.calls.append(("rollback", {}))
        if self.fail_rollback:
            raise RuntimeError("rollback unavailable")

    async def close(self):
        self.calls.append(("close", {}))


class Storage:
    def __init__(self, calls, *, fail_write=False):
        self.calls, self.fail_write = calls, fail_write
        self.original = StorageLocation("original", "existing-object")
        self.sidecar = StorageLocation("markdown", "old-sidecar")
        self.objects = {self.original: b"# Old\n![x](./old.png)\n"}

    async def read(self, location):
        self.calls.append(("read", {"location": location}))
        return self.objects[location]

    async def write(self, location, content, *, content_type):
        self.calls.append(
            (
                "write",
                {
                    "location": location,
                    "content": content,
                    "content_type": content_type,
                },
            )
        )
        if self.fail_write:
            raise RuntimeError("storage unavailable")
        self.objects[location] = content
        return StoredObject(location=location, size=len(content))

    async def delete_quietly(self, location):
        self.calls.append(("delete_quietly", {"location": location}))
        self.objects.pop(location, None)


class KBRepo:
    async def get_by_code(self, cursor, kb_code):
        del cursor, kb_code
        return {"kid": 7}


class FsRepo:
    def __init__(self, calls, *, markdown=True, duplicate_path=None):
        self.calls, self.markdown = calls, markdown
        self.duplicate_path = duplicate_path

    async def get_file_by_path_for_update(
        self, cursor, *, knowledge_base_id, full_path
    ):
        del cursor, knowledge_base_id
        self.calls.append(("lock_file", {"full_path": full_path}))
        return {
            "kid": 8,
            "file_bucket_name": "original",
            "file_object_key": "existing-object",
            "markdown_bucket_name": "markdown" if self.markdown else None,
            "markdown_object_key": "old-sidecar" if self.markdown else None,
            "checksum": "old-checksum",
            "file_size": 22,
        }

    async def update_file_entry_for_update(self, cursor, **kwargs):
        self.calls.append(("update_entry", kwargs))

    async def clear_markdown_metadata(self, cursor, **kwargs):
        self.calls.append(("clear_markdown", kwargs))

    async def lock_checksum_scope(self, cursor, **kwargs):
        self.calls.append(("lock_checksum_scope", kwargs))

    async def get_file_by_checksum(self, cursor, **kwargs):
        self.calls.append(("get_file_by_checksum", kwargs))
        if self.duplicate_path is None:
            return None
        return {"kid": 9, "virtual_path": self.duplicate_path}


class BuildTasks:
    def __init__(self, calls, status=None):
        self.calls, self.status = calls, status

    async def get_latest_by_fs_entry_id(self, cursor, **kwargs):
        del cursor, kwargs
        return None if self.status is None else {"status": self.status}

    async def delete_for_fs_entry_id(self, cursor, **kwargs):
        self.calls.append(("delete_tasks", kwargs))


class Chunks:
    def __init__(self, calls):
        self.calls = calls

    async def delete_for_fs_entry(self, cursor, **kwargs):
        self.calls.append(("delete_chunks", kwargs))


class Projection:
    def __init__(self, calls):
        self.calls = calls

    async def delete_for_fs_entry_ids(self, cursor, **kwargs):
        self.calls.append(("delete_projection", kwargs))


class Cache:
    def __init__(self, calls):
        self.calls = calls

    async def delete_cache_entries_for_fs_entry_ids(self, cursor, **kwargs):
        self.calls.append(("delete_cache", kwargs))


class Metadata:
    def __init__(self, calls, rows=None):
        self.calls = calls
        self.rows = list(rows or [])

    async def upsert_value(self, cursor, **kwargs):
        del cursor
        self.calls.append(("metadata_upsert", kwargs))
        existing = next(
            (
                row
                for row in self.rows
                if row["fs_entry_id"] == kwargs["fs_entry_id"]
                and row["property_name"] == kwargs["property_name"]
                and row["value_type"] == kwargs["value_type"]
            ),
            None,
        )
        if existing is None:
            self.rows.append(dict(kwargs))
        else:
            existing.update(kwargs)

    async def get_file_metadata(self, cursor, *, fs_entry_id, property_names=None):
        del cursor
        self.calls.append(
            (
                "metadata_get",
                {"fs_entry_id": fs_entry_id, "property_names": property_names},
            )
        )
        return [
            row
            for row in self.rows
            if row["fs_entry_id"] == fs_entry_id
            and (property_names is None or row["property_name"] in property_names)
        ]


class References:
    def __init__(self, calls):
        self.calls = calls

    async def delete_outgoing_for_source_fs_entry_id(self, cursor, **kwargs):
        self.calls.append(("delete_outgoing", kwargs))

    async def upsert_relation_assertion(self, cursor, **kwargs):
        self.calls.append(("upsert_assertion", kwargs))
        return {"kid": 300, **kwargs}

    async def list_by_reference_ids(self, cursor, *, reference_ids):
        self.calls.append(("list_tokens", {"reference_ids": reference_ids}))
        return []

    async def resolve_pending_for_path(self, cursor, **kwargs):
        self.calls.append(("resolve_refs", kwargs))
        return []


class Rewriter:
    def __init__(self, calls):
        self.calls = calls

    async def materialize_existing_tokens(self, text, **kwargs):
        del kwargs
        self.calls.append(("materialize_tokens", {"text": text}))
        return text.replace("byqa-ref://41", "./restored.png")

    async def rewrite(self, text, **kwargs):
        self.calls.append(("rewrite", kwargs))
        return text.replace("./new.png", "byqa-ref://9").replace(
            "./restored.png", "byqa-ref://10"
        )


class Timeline:
    def __init__(self, calls):
        self.calls = calls

    async def create_update_event(self, cursor, **kwargs):
        self.calls.append(("timeline", kwargs))
        return {"kid": 99}


class Summary:
    def build_rule_summary(self, old, new):
        del old, new
        return "rule summary"


def build_service(
    calls,
    *,
    fail_commit=False,
    fail_rollback=False,
    fail_write=False,
    markdown=True,
    task_status=None,
    duplicate_path=None,
    metadata_rows=None,
):
    connection, storage = (
        Connection(calls, fail_commit, fail_rollback),
        Storage(calls, fail_write=fail_write),
    )
    service = DocumentUpdateService(
        connection_factory=lambda: _return(connection),
        knowledge_base_repository=KBRepo(),
        knowledge_fs_entry_repository=FsRepo(
            calls, markdown=markdown, duplicate_path=duplicate_path
        ),
        knowledge_item_chunk_repository=Chunks(calls),
        retrieval_projection_repository=Projection(calls),
        knowledge_build_task_repository=BuildTasks(calls, task_status),
        knowledge_fetch_cache_repository=Cache(calls),
        file_metadata_value_repository=Metadata(calls, metadata_rows),
        knowledge_file_reference_repository=References(calls),
        markdown_reference_rewriter=Rewriter(calls),
        storage_provider=storage,
        update_timeline_repository=Timeline(calls),
        markdown_update_summary_service=Summary(),
    )
    return service, connection, storage


async def _return(value):
    return value


def request(content=b"---\ntitle: New\n---\n# New\n![n](./new.png)\n", **kwargs):
    file_path = kwargs.pop("filePath", "/docs/readme.md")
    return DocumentUpdateRequest(
        knCode="kb", filePath=file_path, fileContent=content, **kwargs
    )


async def test_update_rejects_running_build_task_before_storage_mutation():
    calls = []
    service, connection, _ = build_service(calls, task_status="running")
    with pytest.raises(
        KnowledgeBaseValidationError,
        match="File is being built and cannot be updated",
    ):
        await service.update_file(request())
    assert not any(name == "write" for name, _ in calls)
    assert connection.rolled_back


async def test_update_rejects_stale_refer_signature_before_storage_mutation():
    calls = []
    service, connection, _ = build_service(calls)

    with pytest.raises(KnowledgeBaseValidationError, match="file signature mismatch"):
        await service.update_file(request(referSignature="stale-checksum"))

    assert not any(name in {"read", "write", "delete_outgoing"} for name, _ in calls)
    assert connection.rolled_back


async def test_update_accepts_matching_refer_signature():
    calls = []
    service, _, _ = build_service(calls)

    await service.update_file(request(referSignature="old-checksum"))

    assert any(name == "commit" for name, _ in calls)


async def test_update_duplicate_guard_rejects_before_storage_write_and_rolls_back():
    calls = []
    service, connection, _ = build_service(calls, duplicate_path="/docs/existing.md")

    with pytest.raises(
        KnowledgeBaseValidationError,
        match="duplicate file checksum in knowledge base: /docs/existing.md",
    ):
        await service.update_file(request(skipIfDuplicate=True))

    names = [name for name, _ in calls]
    assert "lock_checksum_scope" in names
    assert "get_file_by_checksum" in names
    assert "write" not in names
    assert connection.rolled_back


async def test_storage_failure_does_not_mutate_database():
    calls = []
    service, connection, _ = build_service(calls, fail_write=True)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await service.update_file(request())
    assert not any(name in {"delete_chunks", "update_entry"} for name, _ in calls)
    assert connection.rolled_back


async def test_database_failure_restores_all_old_original_bytes_to_existing_locator():
    calls = []
    service, connection, storage = build_service(calls, fail_commit=True)
    old = storage.objects[storage.original]
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.update_file(request())
    writes = [data for name, data in calls if name == "write"]
    assert [item["location"] for item in writes] == [storage.original, storage.original]
    assert writes[-1]["content"] == old
    assert storage.objects[storage.original] == old
    assert connection.rolled_back


async def test_rollback_failure_still_attempts_storage_restore_and_raises_original_error():
    calls = []
    service, connection, storage = build_service(
        calls, fail_commit=True, fail_rollback=True
    )
    old = storage.objects[storage.original]

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.update_file(request())

    writes = [data for name, data in calls if name == "write"]
    assert writes[-1]["location"] == storage.original
    assert writes[-1]["content"] == old
    assert storage.objects[storage.original] == old
    assert connection.rolled_back


async def test_markdown_update_rewrites_final_bytes_cleans_state_and_records_bounded_context():
    calls = []
    service, _, storage = build_service(calls)
    result = await service.update_file(request(fileDescription=""))
    names = [name for name, _ in calls]
    assert result.timeline_id == 99 and result.is_markdown
    assert storage.objects[storage.original].startswith(b"# New\n")
    assert b"title: New" not in storage.objects[storage.original]
    assert storage.objects[storage.original].endswith(b"byqa-ref://9)\n")
    updated = next(data for name, data in calls if name == "update_entry")
    assert (
        updated["checksum"]
        == hashlib.sha256(storage.objects[storage.original]).hexdigest()
    )
    lock_call = next(data for name, data in calls if name == "lock_checksum_scope")
    assert lock_call == {
        "knowledge_base_id": 7,
        "checksum": updated["checksum"],
    }
    assert updated["description_provided"] is True and updated["file_description"] == ""
    assert (
        "metadata_upsert" in names
        and "delete_outgoing" in names
        and "resolve_refs" in names
    )
    assert (
        names.index("materialize_tokens")
        < names.index("delete_outgoing")
        < names.index("rewrite")
        < names.index("write")
        < names.index("delete_chunks")
        < names.index("timeline")
        < names.index("commit")
    )
    assert names.index("commit") < names.index("delete_quietly")
    assert calls[-2][1]["location"] == storage.sidecar
    assert result.old_markdown_context and result.new_markdown_context


async def test_non_markdown_update_never_decodes_or_calls_llm_and_uses_fixed_summary():
    calls = []
    service, _, storage = build_service(calls, markdown=False)
    storage.objects[storage.original] = b"\xff\x00old"
    result = await service.update_file(
        request(b"\xfe\x01new", filePath="/docs/file.bin")
    )
    event = next(data for name, data in calls if name == "timeline")
    assert (
        not result.is_markdown
        and result.old_markdown_context is None
        and result.new_markdown_context is None
    )
    assert event["summary"] == "文件内容已更新。" and event["summary_source"] == "FIXED"
    assert storage.objects[storage.original] == b"\xfe\x01new"
    metadata_upserts = [data for name, data in calls if name == "metadata_upsert"]
    assert [
        (item["property_name"], item["value_type"], item["value"])
        for item in metadata_upserts
    ] == [("documentKind", "string", "original")]
    assert not any(name == "delete_quietly" for name, _ in calls)
    assert not any(name == "create_task" for name, _ in calls)
    assert any(name == "delete_outgoing" for name, _ in calls)
    assert any(name == "resolve_refs" for name, _ in calls)


async def test_update_backfills_missing_document_kind_and_preserves_explicit_values():
    calls = []
    service, _, _ = build_service(calls)
    await service.update_file(request(b"# Updated\n"))
    document_kind_upserts = [
        data
        for name, data in calls
        if name == "metadata_upsert" and data["property_name"] == "documentKind"
    ]
    assert len(document_kind_upserts) == 1
    assert document_kind_upserts[0]["value"] == "original"

    calls = []
    service, _, _ = build_service(
        calls,
        metadata_rows=[
            {
                "fs_entry_id": 8,
                "knowledge_base_id": 7,
                "property_name": "documentKind",
                "value_type": "string",
                "value": "knowledgeEntity",
            }
        ],
    )
    await service.update_file(request(b"# Updated\n"))
    assert not any(
        name == "metadata_upsert" and data["property_name"] == "documentKind"
        for name, data in calls
    )

    calls = []
    service, _, _ = build_service(calls)
    await service.update_file(
        request(b"---\ndocumentKind: knowledgeEntity\n---\n# Updated\n")
    )
    explicit_upserts = [
        data
        for name, data in calls
        if name == "metadata_upsert" and data["property_name"] == "documentKind"
    ]
    assert len(explicit_upserts) == 1
    assert explicit_upserts[0]["value"] == "knowledgeEntity"


async def test_update_defaults_reserved_directory_file_to_knowledge_entity():
    calls = []
    service, _, _ = build_service(calls)

    await service.update_file(
        request(b"# Entity\n", filePath="/KnowledgeEntity/entity.md")
    )

    document_kind = next(
        data
        for name, data in calls
        if name == "metadata_upsert" and data["property_name"] == "documentKind"
    )
    assert document_kind["value"] == "knowledgeEntity"
    assert not any(
        name == "metadata_upsert" and data["property_name"] == "processingCapabilities"
        for name, data in calls
    )


async def test_update_materializes_old_tokens_before_deleting_and_rewriting_outgoing():
    calls = []
    service, _, storage = build_service(calls)

    await service.update_file(request(b"# New\n![old](byqa-ref://41)\n"))

    names = [name for name, _ in calls]
    assert names.index("materialize_tokens") < names.index("delete_outgoing")
    assert names.index("delete_outgoing") < names.index("rewrite")
    assert storage.objects[storage.original] == b"# New\n![old](byqa-ref://10)\n"


async def test_generated_assertions_are_written_in_same_update_transaction():
    calls = []
    service, _, _ = build_service(calls)
    assertion = GeneratedOutgoingAssertion(
        target_fs_entry_id=77,
        relation_code="PART_OF",
        original_target="Parent",
        discovered_by="ENTITY_ENRICH",
        confidence=0.91,
        definition_version="v2",
        source_task_id=501,
        evidence_fingerprint="fingerprint",
    )

    await service.update_file(
        request(),
        generated_outgoing_assertions=(assertion,),
        producer_run_id="entity-enrich:501",
    )

    generated = next(data for name, data in calls if name == "upsert_assertion")
    assert generated["source_fs_entry_id"] == 8
    assert generated["target_fs_entry_id"] == 77
    assert generated["relation_code"] == "PART_OF"
    assert generated["discovered_by"] == "ENTITY_ENRICH"
    assert generated["producer_run_id"] == "entity-enrich:501"
    assert generated["target_locator_type"] == "ENTITY_SURFACE"
    assert generated["target_locator_value"] == "Parent"
    names = [name for name, _ in calls]
    assert names.index("delete_outgoing") < names.index("upsert_assertion")
    assert names.index("upsert_assertion") < names.index("write")
    assert names.index("upsert_assertion") < names.index("commit")


async def test_update_preserves_absent_description_and_applies_explicit_none():
    calls = []
    service, _, _ = build_service(calls)
    await service.update_file(request())
    absent = next(data for name, data in calls if name == "update_entry")
    assert absent["description_provided"] is False

    calls = []
    service, _, _ = build_service(calls)
    await service.update_file(request(fileDescription=None))
    explicit_none = next(data for name, data in calls if name == "update_entry")
    assert explicit_none["description_provided"] is True
    assert explicit_none["file_description"] is None
