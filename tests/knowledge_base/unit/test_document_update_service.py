"""Unit tests for transactional single-document replacement."""

from __future__ import annotations

import hashlib

import pytest

from by_qa.knowledge_base.api.schemas import DocumentUpdateRequest
from by_qa.knowledge_base.infrastructure.storage import StorageLocation, StoredObject
from by_qa.knowledge_base.services.document_update_service import DocumentUpdateService
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError

pytestmark = pytest.mark.asyncio


class Connection:
    def __init__(self, calls, fail_commit=False):
        self.calls, self.fail_commit, self.cursor_obj = calls, fail_commit, object()
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
    def __init__(self, calls, *, markdown=True):
        self.calls, self.markdown = calls, markdown

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
    def __init__(self, calls):
        self.calls = calls

    async def upsert_value(self, cursor, **kwargs):
        self.calls.append(("frontmatter", kwargs))


class References:
    def __init__(self, calls):
        self.calls = calls

    async def delete_for_source_fs_entry_id(self, cursor, **kwargs):
        self.calls.append(("delete_refs", kwargs))

    async def resolve_pending_for_path(self, cursor, **kwargs):
        self.calls.append(("resolve_refs", kwargs))
        return []


class Rewriter:
    async def rewrite(self, text, **kwargs):
        del kwargs
        return text.replace("./new.png", "byqa-ref://9")


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
    calls, *, fail_commit=False, fail_write=False, markdown=True, task_status=None
):
    connection, storage = (
        Connection(calls, fail_commit),
        Storage(calls, fail_write=fail_write),
    )
    service = DocumentUpdateService(
        connection_factory=lambda: _return(connection),
        knowledge_base_repository=KBRepo(),
        knowledge_fs_entry_repository=FsRepo(calls, markdown=markdown),
        knowledge_item_chunk_repository=Chunks(calls),
        retrieval_projection_repository=Projection(calls),
        knowledge_build_task_repository=BuildTasks(calls, task_status),
        knowledge_fetch_cache_repository=Cache(calls),
        file_metadata_value_repository=Metadata(calls),
        knowledge_file_reference_repository=References(calls),
        markdown_reference_rewriter=Rewriter(),
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
    with pytest.raises(KnowledgeBaseValidationError, match="build task already exists"):
        await service.update_file(request())
    assert not any(name == "write" for name, _ in calls)
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


async def test_markdown_update_rewrites_final_bytes_cleans_state_and_records_bounded_context():
    calls = []
    service, _, storage = build_service(calls)
    result = await service.update_file(request(fileDescription=""))
    names = [name for name, _ in calls]
    assert result.timeline_id == 99 and result.is_markdown
    assert storage.objects[storage.original].endswith(b"byqa-ref://9)\n")
    updated = next(data for name, data in calls if name == "update_entry")
    assert (
        updated["checksum"]
        == hashlib.sha256(storage.objects[storage.original]).hexdigest()
    )
    assert updated["description_provided"] is True and updated["file_description"] == ""
    assert "frontmatter" in names and "delete_refs" in names and "resolve_refs" in names
    assert (
        names.index("delete_refs")
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
    assert not any(name == "frontmatter" for name, _ in calls)
    assert not any(name == "delete_quietly" for name, _ in calls)
    assert not any(name == "create_task" for name, _ in calls)
    assert any(name == "delete_refs" for name, _ in calls)
    assert any(name == "resolve_refs" for name, _ in calls)


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
