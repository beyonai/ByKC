"""Unit tests for the default S3-backed knowledge storage provider."""

# pylint: disable=redefined-outer-name

import pytest
from botocore.exceptions import ClientError

from by_qa.knowledge_base.infrastructure.object_storage import (
    KnowledgeBaseObjectStorage,
)
from by_qa.knowledge_base.infrastructure.storage import (
    KnowledgeStorageProvider,
    StorageLocation,
    StorageNotFoundError,
)


class FakeS3Client:
    def __init__(self):
        self.calls = []
        self.payloads = {}
        self.missing = set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        self.payloads[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    async def get_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.missing or key not in self.payloads:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )

        class Body:
            def __init__(self, data):
                self._data = data

            async def read(self):
                return self._data

        return {"Body": Body(self.payloads[key])}

    async def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))
        self.payloads.pop((kwargs["Bucket"], kwargs["Key"]), None)

    async def copy_object(self, **kwargs):
        self.calls.append(("copy_object", kwargs))
        src = (kwargs["CopySource"]["Bucket"], kwargs["CopySource"]["Key"])
        dst = (kwargs["Bucket"], kwargs["Key"])
        self.payloads[dst] = self.payloads.get(src, b"")

    async def head_bucket(self, **kwargs):
        self.calls.append(("head_bucket", kwargs))

    async def create_bucket(self, **kwargs):
        self.calls.append(("create_bucket", kwargs))

    async def head_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.missing or key not in self.payloads:
            raise ClientError(
                {
                    "Error": {"Code": "NotFound"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {}


class FakeSession:
    def __init__(self, client):
        self._client = client

    def client(self, *_args, **_kwargs):
        return self._client


def _make_storage() -> tuple[KnowledgeBaseObjectStorage, FakeS3Client]:
    client = FakeS3Client()
    storage = KnowledgeBaseObjectStorage(
        session=FakeSession(client),
        endpoint_url="http://localhost:19000",
        access_key="ak",
        secret_key="sk",
        secure=False,
        bucket_name="kb-original",
        markdown_bucket_name="kb-markdown",
    )
    return storage, client


@pytest.fixture
def provider():
    """Yield an S3KnowledgeStorageProvider ready for tests."""
    from by_qa.knowledge_base.infrastructure.storage_s3 import (
        S3KnowledgeStorageProvider,
    )

    storage, _ = _make_storage()
    return S3KnowledgeStorageProvider(storage=storage)


def test_provider_satisfies_protocol(provider):
    assert isinstance(provider, KnowledgeStorageProvider)
    assert provider.provider_name == "minio"
    assert provider.storage_path_bound_to_logical_path is False


def test_build_original_location_uses_fs_entry_id(provider):
    location = provider.build_original_location(
        kb_code="demo",
        knowledge_base_id=7,
        fs_entry_id=42,
        file_path="docs/readme.md",
        mime_type="text/markdown",
    )

    assert location == StorageLocation(
        namespace="kb-original",
        key="kb/7/fs-entry/42/original.md",
    )


def test_build_markdown_location_uses_fs_entry_id(provider):
    location = provider.build_markdown_location(
        kb_code="demo",
        knowledge_base_id=7,
        fs_entry_id=42,
        file_path="docs/readme.md",
    )

    assert location == StorageLocation(
        namespace="kb-markdown",
        key="kb/7/fs-entry/42/markdown.md",
    )


@pytest.mark.asyncio
async def test_write_and_read_round_trip(provider):
    location = StorageLocation(namespace="kb-original", key="x/y")

    stored = await provider.write(location, b"hello", content_type="text/plain")

    assert stored.location == location
    assert stored.size == 5
    assert stored.content_type == "text/plain"
    payload = await provider.read(location)
    assert payload == b"hello"


@pytest.mark.asyncio
async def test_read_translates_missing_key_to_storage_not_found_error():
    storage, fake_client = _make_storage()
    from by_qa.knowledge_base.infrastructure.storage_s3 import (
        S3KnowledgeStorageProvider,
    )

    prov = S3KnowledgeStorageProvider(storage=storage)
    location = StorageLocation(namespace="kb-original", key="missing")
    fake_client.missing.add(("kb-original", "missing"))

    with pytest.raises(StorageNotFoundError):
        await prov.read(location)


@pytest.mark.asyncio
async def test_delete_quietly_swallows_errors():
    storage, fake_client = _make_storage()
    from by_qa.knowledge_base.infrastructure.storage_s3 import (
        S3KnowledgeStorageProvider,
    )

    prov = S3KnowledgeStorageProvider(storage=storage)
    location = StorageLocation(namespace="kb-original", key="absent")

    async def boom(**_kwargs):
        raise ClientError(
            {
                "Error": {"Code": "InternalError"},
                "ResponseMetadata": {"HTTPStatusCode": 500},
            },
            "DeleteObject",
        )

    fake_client.delete_object = boom  # type: ignore[assignment]
    await prov.delete_quietly(location)  # must not raise


@pytest.mark.asyncio
async def test_move_copies_then_deletes_source(provider):
    location = StorageLocation(namespace="kb-original", key="src/a")
    target = StorageLocation(namespace="kb-original", key="dst/a")

    await provider.write(location, b"payload", content_type="application/octet-stream")
    await provider.move(location, target)

    payload = await provider.read(target)
    assert payload == b"payload"

    with pytest.raises(StorageNotFoundError):
        await provider.read(location)
