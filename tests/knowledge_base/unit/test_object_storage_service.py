"""Tests for MinIO-backed KB object storage service."""

from datetime import timedelta

from by_qa.knowledge_base.infrastructure.object_storage import (
    KnowledgeBaseObjectStorage,
)


class FakeMinioClient:
    """Simple MinIO client double."""

    def __init__(self):
        self.put_calls = []
        self.copy_calls = []
        self.remove_calls = []
        self.get_calls = []
        self.object_payloads = {}

    def put_object(self, bucket_name, object_name, data, length, content_type):
        self.put_calls.append(
            (bucket_name, object_name, length, content_type, data.read())
        )

    def copy_object(self, bucket_name, object_name, source):
        self.copy_calls.append((bucket_name, object_name, source))

    def remove_object(self, bucket_name, object_name):
        self.remove_calls.append((bucket_name, object_name))

    def get_object(self, bucket_name, object_name):
        self.get_calls.append((bucket_name, object_name))
        return FakeResponse(self.object_payloads[(bucket_name, object_name)])

    def presigned_get_object(self, bucket_name, object_name, expires):
        return f"https://minio.example/{bucket_name}/{object_name}?ttl={int(expires.total_seconds())}"


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.closed = False
        self.released = False

    def read(self):
        return self._payload

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


def test_build_original_object_key_uses_kb_and_version_hierarchy():
    """Original object keys should follow the documented directory structure."""
    service = KnowledgeBaseObjectStorage(
        client=FakeMinioClient(),
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )

    object_key = service.build_original_object_key(
        knowledge_base_id=7,
        full_path="dir1/hr-policy-001.md",
        version="v2",
    )

    assert object_key == "7/dir1/hr-policy-001.md/v2/hr-policy-001.md"


def test_build_markdown_object_key_uses_markdown_filename_under_original_path():
    """Markdown sidecars should reuse the original path hierarchy with a markdown filename."""
    service = KnowledgeBaseObjectStorage(
        client=FakeMinioClient(),
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )

    object_key = service.build_markdown_object_key(
        knowledge_base_id=7,
        full_path="dir1/hr-policy-001.pdf",
        version="v2",
    )

    assert object_key == "7/dir1/hr-policy-001.pdf/v2/hr-policy-001.md"


def test_upload_temp_object_writes_expected_bucket_prefix_and_content_type():
    """Temp uploads should land under a tmp prefix before promotion."""
    client = FakeMinioClient()
    service = KnowledgeBaseObjectStorage(
        client=client,
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )

    temp_key = service.upload_temp_object(
        "import-1",
        b"# hello",
        content_type="text/markdown; charset=utf-8",
    )

    assert temp_key == "tmp/import-1/content.md"
    assert client.put_calls[0][0] == "knowledge-base"
    assert client.put_calls[0][1] == temp_key
    assert client.put_calls[0][3] == "text/markdown; charset=utf-8"


def test_promote_temp_object_copies_then_removes_temp_object():
    """Promotion should copy to final key and clean the temp object."""
    client = FakeMinioClient()
    service = KnowledgeBaseObjectStorage(
        client=client,
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )

    service.promote_temp_object("tmp/import-1/content.md", "7/item/v1/content.md")

    assert client.copy_calls
    assert client.remove_calls == [("knowledge-base", "tmp/import-1/content.md")]


def test_delete_object_quietly_swallows_removal_errors():
    """Rollback cleanup should not raise if object deletion fails."""

    class BrokenMinioClient(FakeMinioClient):
        def remove_object(self, bucket_name, object_name):
            raise RuntimeError("boom")

    service = KnowledgeBaseObjectStorage(
        client=BrokenMinioClient(),
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )

    service.delete_object_quietly("tmp/import-1/content.md")


def test_download_object_reads_payload_and_releases_connection():
    """Fetch downloads should return bytes and clean up the MinIO response object."""
    client = FakeMinioClient()
    client.object_payloads[("knowledge-base", "7/item/v1/content.md")] = (
        b"# hello\nworld\n"
    )
    service = KnowledgeBaseObjectStorage(
        client=client,
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )

    payload = service.download_object("7/item/v1/content.md")

    assert payload == b"# hello\nworld\n"
    assert client.get_calls == [("knowledge-base", "7/item/v1/content.md")]


def test_build_access_url_uses_presigned_get_object():
    """Binary reads should expose a presigned object URL."""
    client = FakeMinioClient()
    service = KnowledgeBaseObjectStorage(
        client=client,
        bucket_name="knowledge-base",
        markdown_bucket_name="knowledge-base-markdown",
    )

    url = service.build_access_url(
        "7/item/v1/content.pdf", expires=timedelta(minutes=30)
    )

    assert url == "https://minio.example/knowledge-base/7/item/v1/content.pdf?ttl=1800"
