"""Tests for the KB reset script."""

from types import SimpleNamespace

from scripts.reset_kb_data import TARGET_TABLES, reset_minio


def test_reset_kb_data_includes_fetch_cache_index_table():
    """Reset script should also clear the fetch cache index table."""
    assert "knowledge_fetch_cache_index" in TARGET_TABLES


def test_reset_minio_clears_primary_and_markdown_buckets(monkeypatch):
    """Reset should clear both the original-file and markdown sidecar buckets."""

    calls: list[tuple[str, str, str | bool]] = []

    class FakeS3Client:
        def head_bucket(self, Bucket):  # pylint: disable=invalid-name
            calls.append(("head_bucket", Bucket, True))

        def create_bucket(self, Bucket):  # pylint: disable=invalid-name
            calls.append(("create_bucket", Bucket, False))

        def get_paginator(self, operation):  # pylint: disable=unused-argument
            return FakePaginator()

        def delete_object(self, Bucket, Key):  # pylint: disable=invalid-name
            calls.append(("delete_object", Bucket, Key))

    class FakePaginator:
        def paginate(self, Bucket):  # pylint: disable=invalid-name
            return [{"Contents": [{"Key": f"{Bucket}/object"}]}]

    def fake_boto3_client(service_name, **kwargs):  # pylint: disable=unused-argument
        return FakeS3Client()

    monkeypatch.setattr("scripts.reset_kb_data.boto3.client", fake_boto3_client)

    settings = SimpleNamespace(
        kb_minio_endpoint="127.0.0.1:19000",
        kb_minio_access_key="minioadmin",
        kb_minio_secret_key="minioadmin",
        kb_minio_secure=False,
        kb_minio_bucket="knowledge-base",
        kb_minio_markdown_bucket="knowledge-base-markdown",
    )

    reset_minio(settings)

    listed_buckets = {
        bucket_name for action, bucket_name, extra in calls if action == "head_bucket"
    }
    removed_buckets = {
        bucket_name for action, bucket_name, extra in calls if action == "delete_object"
    }

    assert listed_buckets == {"knowledge-base", "knowledge-base-markdown"}
    assert removed_buckets == {"knowledge-base", "knowledge-base-markdown"}
