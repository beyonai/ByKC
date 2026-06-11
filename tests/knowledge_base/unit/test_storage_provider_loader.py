"""Unit tests for storage provider loading via BY_QA_STORAGE_PROVIDER."""

from types import ModuleType, SimpleNamespace

import pytest

from by_qa.knowledge_base.infrastructure.storage import (
    KnowledgeStorageProvider,
    StorageLocation,
    StoredObject,
)


class FakeProvider:
    provider_name = "fake"
    storage_path_bound_to_logical_path = True

    async def ensure_ready(self) -> None:
        return None

    def build_original_location(self, **_kwargs) -> StorageLocation:
        return StorageLocation(namespace="ns", key="orig")

    def build_markdown_location(self, **_kwargs) -> StorageLocation:
        return StorageLocation(namespace="ns", key="md")

    async def write(self, location, content, *, content_type) -> StoredObject:  # pylint: disable=unused-argument
        return StoredObject(
            location=location, size=len(content), content_type=content_type
        )

    async def read(self, location) -> bytes:  # pylint: disable=unused-argument
        return b""

    async def delete(self, location) -> None:  # pylint: disable=unused-argument
        return None

    async def delete_quietly(self, location) -> None:  # pylint: disable=unused-argument
        return None

    async def move(self, source, target, *, overwrite=False) -> None:  # pylint: disable=unused-argument
        return None


class NotAProvider:
    pass


def test_load_storage_provider_defaults_to_s3(monkeypatch):
    """Default startup with MinIO env vars returns S3KnowledgeStorageProvider."""
    from by_qa.knowledge_base.infrastructure.storage_s3 import (
        S3KnowledgeStorageProvider,
    )

    monkeypatch.delenv("BY_QA_STORAGE_PROVIDER", raising=False)
    fake_settings = SimpleNamespace(
        kb_minio_endpoint="localhost:9000",
        kb_minio_access_key="ak",
        kb_minio_secret_key="sk",
        kb_minio_secure=False,
        kb_minio_bucket="kb-original",
        kb_minio_markdown_bucket="kb-markdown",
    )
    monkeypatch.setattr("by_qa.config.get_settings", lambda: fake_settings)

    from by_qa.knowledge_base.infrastructure.storage import load_storage_provider

    provider = load_storage_provider()
    assert isinstance(provider, S3KnowledgeStorageProvider)


def test_load_storage_provider_imports_custom_provider(monkeypatch):
    """A custom provider should be loadable through module:attribute path."""
    module = ModuleType("tests_custom_kb_storage_provider")
    module.FakeProvider = FakeProvider
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    monkeypatch.setenv(
        "BY_QA_STORAGE_PROVIDER",
        "tests_custom_kb_storage_provider:FakeProvider",
    )

    from by_qa.knowledge_base.infrastructure.storage import load_storage_provider

    provider = load_storage_provider()

    assert isinstance(provider, KnowledgeStorageProvider)
    assert isinstance(provider, FakeProvider)


def test_load_storage_provider_rejects_invalid_path(monkeypatch):
    """Malformed env var should raise ValueError."""
    monkeypatch.setenv("BY_QA_STORAGE_PROVIDER", "missing_separator")

    from by_qa.knowledge_base.infrastructure.storage import load_storage_provider

    with pytest.raises(ValueError, match="BY_QA_STORAGE_PROVIDER"):
        load_storage_provider()


def test_load_storage_provider_rejects_non_provider(monkeypatch):
    """A class that doesn't implement KnowledgeStorageProvider should be rejected."""
    module = ModuleType("tests_non_provider_kb_storage")
    module.NotAProvider = NotAProvider
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    monkeypatch.setenv(
        "BY_QA_STORAGE_PROVIDER",
        "tests_non_provider_kb_storage:NotAProvider",
    )

    from by_qa.knowledge_base.infrastructure.storage import load_storage_provider

    with pytest.raises(TypeError, match="KnowledgeStorageProvider"):
        load_storage_provider()
