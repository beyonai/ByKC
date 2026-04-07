"""Tests for the KB reset script."""

from types import SimpleNamespace

from scripts.reset_kb_data import TARGET_TABLES, reset_minio


def test_reset_kb_data_includes_fetch_cache_index_table():
    """Reset script should also clear the fetch cache index table."""
    assert "knowledge_fetch_cache_index" in TARGET_TABLES


def test_reset_minio_clears_primary_and_markdown_buckets(monkeypatch):
    """Reset should clear both the original-file and markdown sidecar buckets."""

    calls: list[tuple[str, str, str | bool]] = []

    class FakeMinio:
        def __init__(self, endpoint, access_key, secret_key, secure):
            calls.append(("init", endpoint, secure))

        def bucket_exists(self, bucket_name):
            calls.append(("bucket_exists", bucket_name, False))
            return True

        def make_bucket(self, bucket_name):
            calls.append(("make_bucket", bucket_name, False))

        def list_objects(self, bucket_name, recursive=True):
            calls.append(("list_objects", bucket_name, recursive))
            return [SimpleNamespace(object_name=f"{bucket_name}/object")]

        def remove_object(self, bucket_name, object_name):
            calls.append(("remove_object", bucket_name, object_name))

    monkeypatch.setattr("scripts.reset_kb_data.Minio", FakeMinio)

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
        bucket_name for action, bucket_name, extra in calls if action == "list_objects"
    }
    removed_buckets = {
        bucket_name for action, bucket_name, extra in calls if action == "remove_object"
    }

    assert listed_buckets == {"knowledge-base", "knowledge-base-markdown"}
    assert removed_buckets == {"knowledge-base", "knowledge-base-markdown"}
