from typing import Any

import yaml

from by_qa.knowledge_base.api.schemas import (
    KnowledgeItemDownloadRequest,
    ReadFileRequest,
)
from by_qa.knowledge_base.infrastructure.storage import StorageLocation
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = object()
        self.closed = False

    def cursor(self) -> object:
        return self.cursor_obj

    async def close(self) -> None:
        self.closed = True


async def _async_return(value: Any) -> Any:
    return value


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor: Any, kb_code: str) -> dict[str, Any]:
        del cursor, kb_code
        return {"kid": 7, "kb_name": "Test KB"}


class FakeFsEntryRepository:
    def __init__(self, file_row: dict[str, Any]) -> None:
        self.file_row = file_row
        self.calls: list[dict[str, Any]] = []

    async def get_file_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any]:
        del cursor
        self.calls.append(
            {"knowledge_base_id": knowledge_base_id, "full_path": full_path}
        )
        return self.file_row


class FakeStorageProvider:
    def __init__(self, payloads: dict[tuple[str, str], bytes]) -> None:
        self.payloads = payloads
        self.reads: list[StorageLocation] = []

    async def read(self, location: StorageLocation) -> bytes:
        self.reads.append(location)
        return self.payloads[(location.namespace, location.key)]


class FakeMarkdownReferenceResolver:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def resolve_texts(
        self, *, knowledge_base_id: int, texts: list[str]
    ) -> list[str]:
        self.calls.append({"knowledge_base_id": knowledge_base_id, "texts": texts})
        return [self.output]


class FakeMetadataRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[int] = []

    async def get_file_metadata(
        self, cursor: Any, *, fs_entry_id: int
    ) -> list[dict[str, Any]]:
        del cursor
        self.calls.append(fs_entry_id)
        return self.rows


def _service(
    *,
    file_row: dict[str, Any],
    payloads: dict[tuple[str, str], bytes],
    resolver: FakeMarkdownReferenceResolver | None,
    metadata_repository: FakeMetadataRepository | None = None,
) -> tuple[KnowledgeBaseService, FakeStorageProvider, FakeFsEntryRepository]:
    connection = FakeConnection()
    storage = FakeStorageProvider(payloads)
    fs_repository = FakeFsEntryRepository(file_row)
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=fs_repository,
        storage_provider=storage,
        markdown_reference_resolver=resolver,
        file_metadata_value_repository=metadata_repository,
    )
    return service, storage, fs_repository


async def test_read_file_slices_lines_before_resolving_tokens():
    resolver = FakeMarkdownReferenceResolver("resolved slice")
    service, storage, fs_repository = _service(
        file_row={
            "kid": 71,
            "markdown_bucket_name": "markdown",
            "markdown_object_key": "kb/7/fs-entry/71/markdown.md",
        },
        payloads={
            (
                "markdown",
                "kb/7/fs-entry/71/markdown.md",
            ): b"line 1 byqa-ref://1\nline 2 byqa-ref://2\nline 3 byqa-ref://3\n"
        },
        resolver=resolver,
    )

    response = await service.read_file(
        ReadFileRequest(
            kb_code="kb",
            file_path="/docs/source.md",
            start_line=2,
            end_line=2,
        )
    )

    assert response["data"] == "resolved slice"
    assert response["startLine"] == 2
    assert response["endLine"] == 2
    assert response["reachedEof"] is False
    assert resolver.calls == [
        {
            "knowledge_base_id": 7,
            "texts": ["line 2 byqa-ref://2\n"],
        }
    ]
    assert storage.reads == [
        StorageLocation(namespace="markdown", key="kb/7/fs-entry/71/markdown.md")
    ]
    assert fs_repository.calls == [
        {"knowledge_base_id": 7, "full_path": "docs/source.md"}
    ]


async def test_download_file_resolves_markdown_before_returning_bytes():
    resolver = FakeMarkdownReferenceResolver("resolved markdown")
    service, storage, fs_repository = _service(
        file_row={
            "kid": 71,
            "file_bucket_name": "original",
            "file_object_key": "kb/7/fs-entry/71/original.md",
            "mime_type": "text/markdown",
        },
        payloads={
            (
                "original",
                "kb/7/fs-entry/71/original.md",
            ): b"# Title\n\n![img](byqa-ref://5)\n"
        },
        resolver=resolver,
    )

    response = await service.download_file(
        KnowledgeItemDownloadRequest(kb_code="kb", file_path="/docs/source.md")
    )

    assert response["content"] == b"resolved markdown"
    assert response["media_type"] == "text/markdown"
    assert storage.reads == [
        StorageLocation(namespace="original", key="kb/7/fs-entry/71/original.md")
    ]
    assert fs_repository.calls == [
        {"knowledge_base_id": 7, "full_path": "docs/source.md"}
    ]
    assert resolver.calls == [
        {
            "knowledge_base_id": 7,
            "texts": ["# Title\n\n![img](byqa-ref://5)\n"],
        }
    ]


async def test_download_file_does_not_resolve_non_markdown_bytes():
    resolver = FakeMarkdownReferenceResolver("should not be used")
    service, storage, fs_repository = _service(
        file_row={
            "kid": 71,
            "file_bucket_name": "original",
            "file_object_key": "kb/7/fs-entry/71/original.pdf",
            "mime_type": "application/pdf",
        },
        payloads={
            (
                "original",
                "kb/7/fs-entry/71/original.pdf",
            ): b"%PDF-1.4 byqa-ref://5"
        },
        resolver=resolver,
    )

    response = await service.download_file(
        KnowledgeItemDownloadRequest(kb_code="kb", file_path="/docs/source.pdf")
    )

    assert response["content"] == b"%PDF-1.4 byqa-ref://5"
    assert response["media_type"] == "application/pdf"
    assert storage.reads == [
        StorageLocation(namespace="original", key="kb/7/fs-entry/71/original.pdf")
    ]
    assert fs_repository.calls == [
        {"knowledge_base_id": 7, "full_path": "docs/source.pdf"}
    ]
    assert resolver.calls == []


async def test_download_file_prepends_current_metadata_as_yaml_front_matter():
    metadata_repository = FakeMetadataRepository(
        [
            {
                "property_name": "title",
                "value_type": "string",
                "value_string": "中文标题",
            },
            {
                "property_name": "published",
                "value_type": "boolean",
                "value_boolean": True,
            },
            {
                "property_name": "tags",
                "value_type": "stringList",
                "value_string_list": '["hr", "policy"]',
            },
        ]
    )
    service, _, _ = _service(
        file_row={
            "kid": 71,
            "file_bucket_name": "original",
            "file_object_key": "kb/7/fs-entry/71/original.md",
            "mime_type": "text/markdown",
        },
        payloads={
            (
                "original",
                "kb/7/fs-entry/71/original.md",
            ): b"---\ntitle: stale storage value\n---\n# Body\n"
        },
        resolver=None,
        metadata_repository=metadata_repository,
    )

    response = await service.download_file(
        KnowledgeItemDownloadRequest(kb_code="kb", file_path="/docs/source.md")
    )

    opening, yaml_text, body = response["content"].decode("utf-8").split("---\n")
    assert opening == ""
    assert yaml.safe_load(yaml_text) == {
        "title": "中文标题",
        "published": True,
        "tags": ["hr", "policy"],
    }
    assert body == "# Body\n"
    assert response["content"].count(b"---\n") == 2
    assert metadata_repository.calls == [71]
