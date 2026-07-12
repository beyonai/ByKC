import hashlib

import pytest

from by_qa.knowledge_base.api.schemas import KnowledgeItemUploadRequest
from by_qa.knowledge_base.infrastructure.storage import StorageLocation, StoredObject
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)

pytestmark = pytest.mark.asyncio


async def _async_return(value):
    return value


class FakeConnection:
    def __init__(self, calls):
        self.calls = calls
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.cursor_obj = object()

    def cursor(self):
        return self.cursor_obj

    async def commit(self):
        self.calls.append(("commit", {}))
        self.committed = True

    async def rollback(self):
        self.calls.append(("rollback", {}))
        self.rolled_back = True

    async def close(self):
        self.closed = True


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor, kb_code):
        return {"kid": 7, "kb_code": kb_code, "kb_name": "Policies"}


class FakeFsEntryRepository:
    def __init__(self, calls):
        self.calls = calls

    async def create_file_entry(
        self, cursor, *, knowledge_base_id, full_path, file_description=None
    ):
        self.calls.append(
            (
                "create_file_entry",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                    "file_description": file_description,
                },
            )
        )
        return {
            "kid": 71,
            "knowledge_base_id": knowledge_base_id,
            "virtual_path": "/" + full_path.strip("/"),
        }

    async def update_file_entry_storage(self, cursor, **kwargs):
        self.calls.append(("update_file_entry_storage", kwargs))

    async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
        self.calls.append(
            (
                "get_file_by_path",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                },
            )
        )
        if full_path == "docs/readme.md":
            return {"kid": 71, "virtual_path": "/docs/readme.md"}
        return None

    async def get_file_reference_target_by_path(
        self, cursor, *, knowledge_base_id, full_path
    ):
        self.calls.append(
            (
                "get_file_reference_target_by_path",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                },
            )
        )
        if full_path == "/assets/logo.png":
            return {"kid": 91}
        return None

    async def get_directory_by_path(self, cursor, *, knowledge_base_id, full_path):
        self.calls.append(
            (
                "get_directory_by_path",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                },
            )
        )
        return None


class FakeStorageProvider:
    def __init__(self, calls, *, fail_write=False):
        self.calls = calls
        self.fail_write = fail_write

    def build_original_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path, mime_type
    ):
        location = StorageLocation(
            namespace="knowledge-base",
            key=f"kb/{knowledge_base_id}/fs-entry/{fs_entry_id}/original.md",
        )
        self.calls.append(
            (
                "build_original_location",
                {
                    "kb_code": kb_code,
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_id": fs_entry_id,
                    "file_path": file_path,
                    "mime_type": mime_type,
                    "location": location,
                },
            )
        )
        return location

    async def write(self, location, content, *, content_type):
        self.calls.append(
            (
                "storage_write",
                {
                    "location": location,
                    "content": content,
                    "content_type": content_type,
                },
            )
        )
        if self.fail_write:
            raise RuntimeError("storage write failed")
        return StoredObject(location=location, size=len(content), checksum="provider")

    async def delete_quietly(self, location):
        self.calls.append(("storage_delete_quietly", {"location": location}))


class FakeReferenceRepository:
    def __init__(self, calls):
        self.calls = calls
        self.next_id = 501

    async def create_reference(self, cursor, **kwargs):
        self.calls.append(("create_reference", kwargs))
        row = {"kid": self.next_id, **kwargs}
        self.next_id += 1
        return row

    async def resolve_pending_for_path(self, cursor, **kwargs):
        self.calls.append(("resolve_pending_for_path", kwargs))
        return []

    async def rebind_deleted_target_for_path(self, cursor, **kwargs):
        self.calls.append(("rebind_deleted_target_for_path", kwargs))
        return []


class RebindingReferenceRepository(FakeReferenceRepository):
    def __init__(self, calls):
        super().__init__(calls)
        self.deleted_targets = {
            41: {"virtual_path": "/target/b.md", "is_deleted": True}
        }
        self.references = [
            {
                "kid": 601,
                "status": "resolved",
                "target_fs_entry_id": 41,
                "target_path": None,
            }
        ]

    async def rebind_deleted_target_for_path(self, cursor, **kwargs):
        self.calls.append(("rebind_deleted_target_for_path", kwargs))
        rebound = []
        for reference in self.references:
            deleted_target = self.deleted_targets.get(reference["target_fs_entry_id"])
            if (
                reference["status"] == "resolved"
                and deleted_target is not None
                and deleted_target["is_deleted"] is True
                and deleted_target["virtual_path"] == kwargs["target_path"]
                and reference["target_fs_entry_id"] != kwargs["target_fs_entry_id"]
            ):
                reference["target_fs_entry_id"] = kwargs["target_fs_entry_id"]
                reference["target_path"] = None
                rebound.append(dict(reference))
        return rebound


class FakeMarkdownReferenceRewriter:
    def __init__(self, calls):
        self.calls = calls

    async def rewrite(self, text, current_dir=None, kb_code=None, **kwargs):
        self.calls.append(
            (
                "rewrite",
                {
                    "text": text,
                    "current_dir": current_dir,
                    "kb_code": kb_code,
                    **kwargs,
                },
            )
        )
        await kwargs["reference_repository"].create_reference(
            kwargs["cursor"],
            knowledge_base_id=kwargs["knowledge_base_id"],
            source_fs_entry_id=kwargs["source_fs_entry_id"],
            target_fs_entry_id=None,
            original_target="./later.png",
            target_path="/docs/later.png",
            target_suffix="",
            target_kind="FILE",
            status="unresolved",
        )
        return text.replace("./later.png", "byqa-ref://501")


class FailingMarkdownReferenceRewriter(FakeMarkdownReferenceRewriter):
    async def rewrite(self, text, current_dir=None, kb_code=None, **kwargs):
        await super().rewrite(text, current_dir=current_dir, kb_code=kb_code, **kwargs)
        return text.replace("./later.png", "byqa-ref://501")


def _build_service(
    calls,
    *,
    storage=None,
    reference_repository=None,
    rewriter=None,
):
    connection = FakeConnection(calls)
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=FakeFsEntryRepository(calls),
        knowledge_item_chunk_repository=object(),
        retrieval_projection_repository=object(),
        storage_provider=storage or FakeStorageProvider(calls),
        embedding_dimension=2,
        knowledge_file_reference_repository=reference_repository
        or FakeReferenceRepository(calls),
        markdown_reference_rewriter=rewriter or FakeMarkdownReferenceRewriter(calls),
    )
    return service, connection


def _call_names(calls):
    return [call[0] for call in calls]


async def test_markdown_upload_rewrites_inside_transaction_before_storage_and_commits():
    calls = []
    service, connection = _build_service(calls)

    result = await service.upload_file(
        KnowledgeItemUploadRequest(
            knCode="kb-1",
            filePath="/docs/readme.md",
            fileContent=b"---\ntitle: Doc\n---\n![later](./later.png)\n",
        )
    )

    names = _call_names(calls)
    assert names.index("create_file_entry") < names.index("rewrite")
    assert names.index("rewrite") < names.index("storage_write")
    assert names.index("update_file_entry_storage") < names.index(
        "resolve_pending_for_path"
    )
    assert names.index("resolve_pending_for_path") < names.index("commit")

    storage_call = calls[names.index("storage_write")][1]
    assert storage_call["content"] == (
        b"---\ntitle: Doc\n---\n![later](byqa-ref://501)\n"
    )
    assert storage_call["content_type"] == "text/markdown"

    update_call = calls[names.index("update_file_entry_storage")][1]
    assert update_call["file_size"] == len(storage_call["content"])
    assert update_call["mime_type"] == "text/markdown"
    assert (
        update_call["checksum"]
        == hashlib.sha256(b"---\ntitle: Doc\n---\n![later](./later.png)\n").hexdigest()
    )
    assert (
        update_call["checksum"] != hashlib.sha256(storage_call["content"]).hexdigest()
    )

    pending_call = calls[names.index("resolve_pending_for_path")][1]
    assert pending_call == {
        "knowledge_base_id": 7,
        "target_path": "/docs/readme.md",
        "target_fs_entry_id": 71,
    }
    assert result == {
        "fs_entry_id": 71,
        "knowledge_base_id": 7,
        "virtual_path": "/docs/readme.md",
        "mime_type": "text/markdown",
    }
    assert connection.committed is True
    assert connection.rolled_back is False


async def test_storage_write_failure_rolls_back_references_and_runs_cleanup():
    calls = []
    service, connection = _build_service(
        calls,
        storage=FakeStorageProvider(calls, fail_write=True),
    )

    with pytest.raises(RuntimeError, match="storage write failed"):
        await service.upload_file(
            KnowledgeItemUploadRequest(
                knCode="kb-1",
                filePath="/docs/readme.md",
                fileContent=b"![later](./later.png)\n",
            )
        )

    names = _call_names(calls)
    assert "create_reference" in names
    assert names.index("create_reference") < names.index("storage_write")
    assert names.index("rollback") < names.index("storage_delete_quietly")
    assert "commit" not in names
    assert connection.rolled_back is True
    assert connection.committed is False


async def test_non_markdown_upload_does_not_call_rewriter_but_resolves_pending():
    calls = []
    service, connection = _build_service(calls)

    result = await service.upload_file(
        KnowledgeItemUploadRequest(
            knCode="kb-1",
            filePath="/docs/manual.pdf",
            fileContent=b"%PDF bytes",
        )
    )

    names = _call_names(calls)
    assert "rewrite" not in names
    assert names.index("update_file_entry_storage") < names.index(
        "resolve_pending_for_path"
    )
    assert calls[names.index("storage_write")][1]["content"] == b"%PDF bytes"
    update_call = calls[names.index("update_file_entry_storage")][1]
    assert update_call["checksum"] == hashlib.sha256(b"%PDF bytes").hexdigest()
    assert result["fs_entry_id"] == 71
    assert result["knowledge_base_id"] == 7
    assert result["virtual_path"] == "/docs/manual.pdf"
    assert result["mime_type"] == "application/pdf"
    assert connection.committed is True


async def test_resolve_pending_references_for_paths_resolves_existing_targets_once():
    calls = []
    service, connection = _build_service(calls)

    result = await service.resolve_pending_references_for_paths(
        kb_code="kb-1",
        file_paths=["/docs/readme.md", "docs/readme.md", "/docs/missing.md"],
    )

    assert result == []
    names = _call_names(calls)
    get_file_calls = [call for call in calls if call[0] == "get_file_by_path"]
    assert get_file_calls == [
        (
            "get_file_by_path",
            {"knowledge_base_id": 7, "full_path": "docs/readme.md"},
        ),
        (
            "get_file_by_path",
            {"knowledge_base_id": 7, "full_path": "docs/missing.md"},
        ),
    ]
    pending_calls = [call for call in calls if call[0] == "resolve_pending_for_path"]
    assert pending_calls == [
        (
            "resolve_pending_for_path",
            {
                "knowledge_base_id": 7,
                "target_path": "/docs/readme.md",
                "target_fs_entry_id": 71,
            },
        )
    ]
    assert names[-1] == "commit"
    assert connection.committed is True
    assert connection.rolled_back is False


async def test_resolve_pending_references_for_uploaded_rows_skips_path_lookup():
    calls = []
    service, connection = _build_service(calls)

    result = await service.resolve_pending_references_for_paths(
        kb_code="kb-1",
        uploaded_rows=[
            {
                "fs_entry_id": 81,
                "knowledge_base_id": 7,
                "virtual_path": "/docs/readme.md",
            },
            {
                "kid": 82,
                "knowledge_base_id": 7,
                "file_path": "docs/manual.pdf",
            },
        ],
    )

    assert result == []
    names = _call_names(calls)
    assert "get_file_by_path" not in names
    pending_calls = [call for call in calls if call[0] == "resolve_pending_for_path"]
    assert pending_calls == [
        (
            "resolve_pending_for_path",
            {
                "knowledge_base_id": 7,
                "target_path": "/docs/readme.md",
                "target_fs_entry_id": 81,
            },
        ),
        (
            "resolve_pending_for_path",
            {
                "knowledge_base_id": 7,
                "target_path": "/docs/manual.pdf",
                "target_fs_entry_id": 82,
            },
        ),
    ]
    assert names[-1] == "commit"
    assert connection.committed is True
    assert connection.rolled_back is False


async def test_resolve_pending_references_for_uploaded_rows_rebinds_deleted_targets():
    calls = []
    reference_repository = RebindingReferenceRepository(calls)
    service, connection = _build_service(
        calls,
        reference_repository=reference_repository,
    )

    result = await service.resolve_pending_references_for_paths(
        kb_code="kb-1",
        uploaded_rows=[
            {
                "fs_entry_id": 81,
                "knowledge_base_id": 7,
                "virtual_path": "/target/b.md",
            },
        ],
    )
    names = _call_names(calls)
    assert "get_file_by_path" not in names
    assert names.index("resolve_pending_for_path") < names.index(
        "rebind_deleted_target_for_path"
    )
    rebind_calls = [
        call for call in calls if call[0] == "rebind_deleted_target_for_path"
    ]
    assert rebind_calls == [
        (
            "rebind_deleted_target_for_path",
            {
                "knowledge_base_id": 7,
                "target_path": "/target/b.md",
                "target_fs_entry_id": 81,
            },
        )
    ]
    assert result == [
        {
            "kid": 601,
            "status": "resolved",
            "target_fs_entry_id": 81,
            "target_path": None,
        }
    ]
    assert reference_repository.references == [
        {
            "kid": 601,
            "status": "resolved",
            "target_fs_entry_id": 81,
            "target_path": None,
        }
    ]
    assert names[-1] == "commit"
    assert connection.committed is True
    assert connection.rolled_back is False


async def test_resolve_pending_references_falls_back_when_uploaded_rows_malformed():
    calls = []
    service, connection = _build_service(calls)

    result = await service.resolve_pending_references_for_paths(
        kb_code="kb-1",
        file_paths=["/docs/readme.md"],
        uploaded_rows=[{"unexpected": "row"}],
    )

    assert result == []
    get_file_calls = [call for call in calls if call[0] == "get_file_by_path"]
    assert get_file_calls == [
        (
            "get_file_by_path",
            {"knowledge_base_id": 7, "full_path": "docs/readme.md"},
        )
    ]
    pending_calls = [call for call in calls if call[0] == "resolve_pending_for_path"]
    assert pending_calls == [
        (
            "resolve_pending_for_path",
            {
                "knowledge_base_id": 7,
                "target_path": "/docs/readme.md",
                "target_fs_entry_id": 71,
            },
        )
    ]
    assert connection.committed is True
    assert connection.rolled_back is False


async def test_resolve_pending_references_falls_back_per_path_for_malformed_rows():
    calls = []
    service, connection = _build_service(calls)

    result = await service.resolve_pending_references_for_paths(
        kb_code="kb-1",
        file_paths=["/docs/manual.pdf", "/docs/readme.md"],
        uploaded_rows=[
            {
                "fs_entry_id": 82,
                "knowledge_base_id": 7,
                "virtual_path": "/docs/manual.pdf",
            },
            {"unexpected": "row"},
        ],
    )

    assert result == []
    get_file_calls = [call for call in calls if call[0] == "get_file_by_path"]
    assert get_file_calls == [
        (
            "get_file_by_path",
            {"knowledge_base_id": 7, "full_path": "docs/readme.md"},
        )
    ]
    pending_calls = [call for call in calls if call[0] == "resolve_pending_for_path"]
    assert pending_calls == [
        (
            "resolve_pending_for_path",
            {
                "knowledge_base_id": 7,
                "target_path": "/docs/manual.pdf",
                "target_fs_entry_id": 82,
            },
        ),
        (
            "resolve_pending_for_path",
            {
                "knowledge_base_id": 7,
                "target_path": "/docs/readme.md",
                "target_fs_entry_id": 71,
            },
        ),
    ]
    assert connection.committed is True
    assert connection.rolled_back is False
