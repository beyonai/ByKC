"""Tests for async S3-backed KB object storage service."""

from datetime import timedelta

from by_qa.knowledge_base.infrastructure.object_storage import (
    KnowledgeBaseObjectStorage,
)


class FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class FakeS3Client:
    def __init__(self):
        self.calls = []
        self._payloads = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))

    async def copy_object(self, **kwargs):
        self.calls.append(("copy_object", kwargs))

    async def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))

    async def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        key = (kwargs["Bucket"], kwargs["Key"])
        return {"Body": FakeBody(self._payloads.get(key, b""))}

    async def head_bucket(self, **kwargs):
        self.calls.append(("head_bucket", kwargs))

    async def create_bucket(self, **kwargs):
        self.calls.append(("create_bucket", kwargs))

    async def generate_presigned_url(self, _client_method, Params, _expires_in):  # pylint: disable=invalid-name
        return f"https://example.test/{Params['Bucket']}/{Params['Key']}"


class FakeSession:
    def __init__(self, client):
        self._client = client

    def client(self, service_name, **kwargs):  # pylint: disable=unused-argument
        return self._client


def _make_service(client):
    return KnowledgeBaseObjectStorage(
        session=FakeSession(client),
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )


def test_build_original_object_key_uses_stable_item_identity_hierarchy():
    """Original object keys should be stable across path renames."""
    service = _make_service(FakeS3Client())

    object_key = service.build_original_object_key(
        knowledge_base_id=7,
        knowledge_item_id=42,
        version="v2",
    )

    assert object_key == "kb/7/item/42/version/v2/original"


def test_build_markdown_object_key_uses_stable_item_identity_hierarchy():
    """Markdown sidecars should no longer depend on the source path."""
    service = _make_service(FakeS3Client())

    object_key = service.build_markdown_object_key(
        knowledge_base_id=7,
        knowledge_item_id=42,
        version="v2",
    )

    assert object_key == "kb/7/item/42/version/v2/markdown"


async def test_upload_temp_object_writes_expected_bucket_prefix_and_content_type():
    """Temp uploads should land under a tmp prefix before promotion."""
    client = FakeS3Client()
    service = _make_service(client)

    temp_key = await service.upload_temp_object(
        "import-1",
        b"# hello",
        content_type="text/markdown; charset=utf-8",
    )

    assert temp_key == "tmp/import-1/content.md"
    call_name, call_kwargs = client.calls[0]
    assert call_name == "put_object"
    assert call_kwargs["Bucket"] == "knowledge-base"
    assert call_kwargs["Key"] == temp_key
    assert call_kwargs["ContentType"] == "text/markdown; charset=utf-8"


async def test_promote_temp_object_copies_then_removes_temp_object():
    """Promotion should copy to final key and clean the temp object."""
    client = FakeS3Client()
    service = _make_service(client)

    await service.promote_temp_object("tmp/import-1/content.md", "7/item/v1/content.md")

    call_names = [c[0] for c in client.calls]
    assert "copy_object" in call_names
    assert "delete_object" in call_names

    delete_call = next(c for c in client.calls if c[0] == "delete_object")
    assert delete_call[1]["Bucket"] == "knowledge-base"
    assert delete_call[1]["Key"] == "tmp/import-1/content.md"


async def test_delete_object_quietly_swallows_removal_errors():
    """Rollback cleanup should not raise if object deletion fails."""

    class BrokenS3Client(FakeS3Client):
        async def delete_object(self, **kwargs):
            raise RuntimeError("boom")

    service = _make_service(BrokenS3Client())

    await service.delete_object_quietly("tmp/import-1/content.md")


async def test_download_object_reads_payload_and_releases_connection():
    """Fetch downloads should return bytes from the S3 response body."""
    client = FakeS3Client()
    client._payloads[("knowledge-base", "7/item/v1/content.md")] = b"# hello\nworld\n"
    service = _make_service(client)

    payload = await service.download_object("7/item/v1/content.md")

    assert payload == b"# hello\nworld\n"
    assert client.calls[0] == (
        "get_object",
        {"Bucket": "knowledge-base", "Key": "7/item/v1/content.md"},
    )


async def test_build_access_url_uses_presigned_get_object():
    """Binary reads should expose a presigned object URL."""
    client = FakeS3Client()
    service = _make_service(client)

    url = await service.build_access_url(
        "7/item/v1/content.pdf", expires=timedelta(minutes=30)
    )

    assert "knowledge-base" in url
    assert "7/item/v1/content.pdf" in url


async def test_ensure_buckets_creates_missing_buckets():
    """ensure_buckets should check and create both buckets when they are missing."""

    class MissingBucketS3Client(FakeS3Client):
        async def head_bucket(self, **kwargs):
            self.calls.append(("head_bucket", kwargs))
            raise RuntimeError("NoSuchBucket")

    client = MissingBucketS3Client()
    service = _make_service(client)

    await service.ensure_buckets()

    call_names = [c[0] for c in client.calls]
    assert call_names.count("head_bucket") == 2
    assert call_names.count("create_bucket") == 2
