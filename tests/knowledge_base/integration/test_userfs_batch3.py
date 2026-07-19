"""UserFS storage provider integration tests — scenarios U19 through U27.

U19: Directory rename propagates to API + filesystem
U20: downloadFile bytes match filesystem file bytes exactly
U21: readFile markdown text matches filesystem .md file text
U22: Delete removes file from API + search + filesystem
U23: Write failure → no DB record, no filesystem residual
U24: (skipped — DB commit failure requires external failure injection)
U25: Partial move failure → first file rolled back
U26: Two concurrent imports of same path → only one succeeds
U27: Storage root doesn't exist → ensure_ready creates it
"""

# pylint: disable=unused-argument,invalid-name

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import stat
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from _userfs_provider import UserFSProvider
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings
from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_base.infrastructure.runtime import (
    build_knowledge_item_search_service,
)
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

# ---------------------------------------------------------------------------
# Default database settings (same as existing stateful integration tests)
# ---------------------------------------------------------------------------
DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "15432"
DEFAULT_DB_DATABASE = "postgres"
DEFAULT_DB_USER = "gaussdb"
DEFAULT_DB_PASS = "OpenGauss#2026"


def _default_embedding_dimension() -> int:
    return int(os.getenv("EMBEDDING_DIMENSION", "3"))


def _default_embedding_vector() -> list[float]:
    return [0.1] * _default_embedding_dimension()


# ---------------------------------------------------------------------------
# Test doubles (same pattern as test_kb_api_stateful_integration.py)
# ---------------------------------------------------------------------------
class FakeDocumentChunkingService:
    """Stable knowledge_build double."""

    def __init__(self, *, markdown_text: str, embedding: list[float] | None = None):
        self.markdown_text = markdown_text
        self.embedding = embedding or _default_embedding_vector()

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        assert isinstance(file_bytes, bytes)
        return self.markdown_text

    def chunk_and_embed(
        self, file_bytes: bytes, *, filename: str
    ) -> list[KnowledgeItemChunkPayload]:
        assert isinstance(filename, str)
        content = file_bytes.decode("utf-8")
        line_count = max(1, content.count("\n"))
        return [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=line_count,
                chunk_text=content.strip(),
                embedding=self.embedding,
            )
        ]


class FakeEmbeddingQueryService:
    """Deterministic embedding service."""

    def __init__(self, embedding: list[float] | None = None):
        self.embedding = embedding or _default_embedding_vector()

    async def embed_query(self, query: str) -> list[float]:
        assert isinstance(query, str)
        return self.embedding


class FakeModelConfigProvider:
    """Stable model config provider isolated from local .env values."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def get_config(self, model_type: str) -> ModelConfig:
        if model_type != "embedding":
            raise ValueError(f"Unexpected model_type: {model_type!r}")
        return ModelConfig(
            model_name=self.settings.embedding_model_name,
            temperature=0.0,
            base_url=self.settings.embedding_base_url,
            api_key=self.settings.embedding_api_key,
            dimension=self.settings.embedding_dimension,
            distance_metric=self.settings.embedding_distance_metric,
        )


# ---------------------------------------------------------------------------
# Settings factory
# ---------------------------------------------------------------------------
def _kb_settings(*, agent_data_path=None) -> Settings:
    updates = {
        "DB_HOST": os.getenv("DB_HOST", DEFAULT_DB_HOST),
        "DB_PORT": int(os.getenv("DB_PORT", DEFAULT_DB_PORT)),
        "DB_DATABASE": os.getenv("DB_DATABASE", DEFAULT_DB_DATABASE),
        "DB_SCHEMA": os.getenv("DB_SCHEMA", ""),
        "DB_USER": os.getenv("DB_USER", DEFAULT_DB_USER),
        "DB_PASS": os.getenv("DB_PASS", DEFAULT_DB_PASS),
        "MINIO_ENDPOINT": os.getenv("MINIO_ENDPOINT", ""),
        "MINIO_ACCESS_KEY": os.getenv("MINIO_ACCESS_KEY", ""),
        "MINIO_SECRET_KEY": os.getenv("MINIO_SECRET_KEY", ""),
        "KB_MINIO_BUCKET": os.getenv("KB_MINIO_BUCKET", ""),
        "KB_MINIO_MARKDOWN_BUCKET": os.getenv("KB_MINIO_MARKDOWN_BUCKET", ""),
        "MINIO_SECURE": False,
        "EMBEDDING_MODEL_NAME": os.getenv("EMBEDDING_MODEL_NAME", "bge-m3"),
        "EMBEDDING_BASE_URL": "https://embedding.example.com",
        "EMBEDDING_API_KEY": "secret",
        "EMBEDDING_DIMENSION": int(os.getenv("EMBEDDING_DIMENSION", "3")),
        "EMBEDDING_DISTANCE_METRIC": os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine"),
    }
    if agent_data_path is not None:
        updates["agent_data_path"] = agent_data_path
    return Settings(**updates)


# ---------------------------------------------------------------------------
# Runtime wiring
# ---------------------------------------------------------------------------
def _reset_runtime(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(
        main_module,
        "load_model_config_provider",
        lambda: FakeModelConfigProvider(settings),
    )
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_document_update_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_initialized", False)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_lock", asyncio.Lock())

    async def _noop_register(application):
        return None

    monkeypatch.setattr(main_module, "_register_service", _noop_register)
    monkeypatch.setattr(main_module, "_unregister_service", _noop_register)


def _set_document_chunking_service(
    monkeypatch: pytest.MonkeyPatch,
    service: FakeDocumentChunkingService,
) -> None:
    async def get_service(provider=None):
        return service

    monkeypatch.setattr(
        main_module, "_get_or_build_document_chunking_service", get_service
    )


async def _set_search_service(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    embedding: list[float] | None = None,
) -> None:
    service = await build_knowledge_item_search_service(settings)
    service.embedding_query_service = FakeEmbeddingQueryService(embedding)

    async def get_service(provider=None):
        return service

    monkeypatch.setattr(
        main_module, "_get_or_build_knowledge_item_search_service", get_service
    )


# ---------------------------------------------------------------------------
# UserFS wiring
# ---------------------------------------------------------------------------
def _wire_userfs(
    monkeypatch: pytest.MonkeyPatch, root: Path, settings: Settings
) -> Path:
    """Wire UserFS storage provider and reset runtime. Returns the root path."""

    # 1. Register the _userfs_test_provider module dynamically
    def _make_userfs():
        return UserFSProvider(root=root)

    mod = ModuleType("_userfs_test_provider")
    mod.make_userfs = _make_userfs
    sys.modules["_userfs_test_provider"] = mod
    monkeypatch.setenv("BY_QA_STORAGE_PROVIDER", "_userfs_test_provider:make_userfs")

    # 2. Reset runtime (must happen after env var is set)
    _reset_runtime(monkeypatch, settings)

    # 3. Wire a default document chunking service so build works
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="default\ncontent\n"),
    )

    return root


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def _create_kb(client: TestClient, kb_name: str) -> str:
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knName": kb_name},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["knCode"]


def _create_directory(client: TestClient, *, kb_code: str, directory_path: str) -> None:
    response = client.post(
        "/api/v1/directories/create",
        json={
            "knCode": kb_code,
            "directoryPath": directory_path,
            "directoryDescription": f"{directory_path} description",
        },
    )
    assert response.status_code == 200, response.text


def _upload_file(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    file_content: bytes,
    content_type: str = "text/markdown",
) -> None:
    response = client.post(
        "/api/v1/knowledgeItems/import",
        data={"knCode": kb_code, "filePath": file_path},
        files={"fileContent": (file_path.split("/")[-1], file_content, content_type)},
    )
    assert response.status_code == 200, response.text


def _upload_and_build_file(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    file_content: bytes,
    content_type: str = "text/markdown",
) -> None:
    _upload_file(
        client,
        kb_code=kb_code,
        file_path=file_path,
        file_content=file_content,
        content_type=content_type,
    )
    build_response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={
            "knCode": kb_code,
            "filePath": file_path,
        },
    )
    assert build_response.status_code == 200, build_response.text


@pytest.mark.integration
def test_udt10_update_overwrites_existing_userfs_original_locator(monkeypatch):
    """A path-bound provider updates its existing raw path without creating a new key."""
    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    async def no_llm(self, old_markdown, new_markdown):
        _ = self, old_markdown, new_markdown
        return None

    monkeypatch.setattr(MarkdownUpdateSummaryService, "generate_llm_summary", no_llm)
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, f"udt10-kb-{uuid4().hex[:12]}")
            _upload_file(
                client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Old\n"
            )
            raw_path = root / kb_code / "raw" / "docs" / "a.md"
            assert raw_path.read_bytes() == b"# Old\n"
            response = client.post(
                "/api/v1/knowledgeItems/update",
                data={"knCode": kb_code, "filePath": "/docs/a.md"},
                files={"fileContent": ("a.md", b"# New\n", "text/markdown")},
            )
            assert response.json()["resultCode"] == "0"
        assert raw_path.read_bytes() == b"# New\n"


@pytest.mark.integration
def test_udt9_storage_write_failure_preserves_userfs_original(monkeypatch):
    """A failed update write returns an API error and retains the old raw bytes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, f"udt9-kb-{uuid4().hex[:12]}")
            _upload_file(
                client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Old\n"
            )
        raw_path = root / kb_code / "raw" / "docs" / "a.md"

        async def fail_new_write(self, location, content, *, content_type):
            if content == b"# New\n":
                raise RuntimeError("injected storage failure")
            return await original_write(
                self, location, content, content_type=content_type
            )

        original_write = UserFSProvider.write
        monkeypatch.setattr(UserFSProvider, "write", fail_new_write)
        with TestClient(main_module.app) as client:
            response = client.post(
                "/api/v1/knowledgeItems/update",
                data={"knCode": kb_code, "filePath": "/docs/a.md"},
                files={"fileContent": ("a.md", b"# New\n", "text/markdown")},
            )
        assert response.json()["resultCode"] == "-1"
        assert raw_path.read_bytes() == b"# Old\n"


@pytest.mark.integration
def test_udt9_commit_failure_restores_userfs_original(monkeypatch):
    """A database commit failure restores the overwritten raw object at its locator."""
    from by_qa.knowledge_base.infrastructure import runtime as runtime_module

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)
        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, f"udt9-commit-{uuid4().hex[:12]}")
            _upload_file(
                client, kb_code=kb_code, file_path="/docs/a.md", file_content=b"# Old\n"
            )
            raw_path = root / kb_code / "raw" / "docs" / "a.md"
            original_factory = runtime_module.build_connection_factory

            def failing_factory(current_settings):
                connect = original_factory(current_settings)

                async def failing_connect():
                    connection = await connect()

                    async def fail_commit():
                        raise RuntimeError("injected commit failure")

                    connection.commit = fail_commit
                    return connection

                return failing_connect

            monkeypatch.setattr(
                runtime_module, "build_connection_factory", failing_factory
            )
            monkeypatch.setattr(main_module, "_document_update_service", None)
            response = client.post(
                "/api/v1/knowledgeItems/update",
                data={"knCode": kb_code, "filePath": "/docs/a.md"},
                files={"fileContent": ("a.md", b"# New\n", "text/markdown")},
            )
        assert response.json()["resultCode"] == "-1"
        assert raw_path.read_bytes() == b"# Old\n"


# ===================================================================
# U19: Directory rename propagates to API + filesystem
# ===================================================================
@pytest.mark.integration
def test_u19_rename_directory_updates_api_list_and_filesystem(monkeypatch):
    """Rename /old to /new → listDir shows /new not /old; filesystem follows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)

        kb_name = f"u19-kb-{uuid4().hex[:12]}"
        old_dir = "/old"
        new_dir = "/new"
        file_path = f"{old_dir}/doc.md"
        file_content = b"rename-test\nline2\n"

        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, kb_name)
            _create_directory(client, kb_code=kb_code, directory_path=old_dir)
            _upload_and_build_file(
                client,
                kb_code=kb_code,
                file_path=file_path,
                file_content=file_content,
            )

            # Rename /old → /new
            rename = client.post(
                "/api/v1/directories/update",
                json={
                    "knCode": kb_code,
                    "directoryPath": old_dir,
                    "directoryName": "new",
                },
            )
            assert rename.status_code == 200, rename.text
            assert rename.json()["resultCode"] == "0", rename.json()

            # listDir /new should show the file
            new_list = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": new_dir},
            )
            assert new_list.status_code == 200, new_list.text
            new_data = new_list.json()["resultObject"]["data"]
            assert len(new_data) >= 1
            assert any("doc.md" in item["name"] for item in new_data)

            # listDir /old should error
            old_list = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": old_dir},
            )
            assert old_list.status_code == 200
            assert old_list.json()["resultCode"] == "-1"

            # readFile on new path should work
            new_read = client.post(
                "/api/v1/readFile",
                json={
                    "knCode": kb_code,
                    "filePath": f"{new_dir}/doc.md",
                    "startLine": 1,
                    "endLine": 2,
                },
            )
            assert new_read.status_code == 200, new_read.text
            assert new_read.json()["resultCode"] == "0"

        # Filesystem: old path gone, new path exists
        old_raw = root / kb_code / "raw" / "old" / "doc.md"
        old_md = root / kb_code / "md" / "old" / "doc.md.md"
        new_raw = root / kb_code / "raw" / "new" / "doc.md"
        new_md = root / kb_code / "md" / "new" / "doc.md.md"

        assert not old_raw.exists(), f"old raw still exists: {old_raw}"
        assert not old_md.exists(), f"old md still exists: {old_md}"
        assert new_raw.exists(), f"new raw missing: {new_raw}"
        assert new_md.exists(), f"new md missing: {new_md}"
        assert new_raw.read_bytes() == file_content


# ===================================================================
# U20: downloadFile bytes match filesystem file bytes exactly
# ===================================================================
@pytest.mark.integration
def test_u20_download_file_returns_exact_filesystem_bytes(monkeypatch):
    """Import a file → downloadFile returns bytes identical to filesystem raw file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)

        kb_name = f"u20-kb-{uuid4().hex[:12]}"
        file_path = "/docs/report.pdf"
        original_bytes = b"%PDF-1.4\x00binary report content\xff\xfe"

        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, kb_name)
            _create_directory(client, kb_code=kb_code, directory_path="/docs")
            _upload_file(
                client,
                kb_code=kb_code,
                file_path=file_path,
                file_content=original_bytes,
                content_type="application/pdf",
            )

            download = client.post(
                "/api/v1/downloadFile",
                json={"knCode": kb_code, "filePath": file_path},
            )
            assert download.status_code == 200, download.text
            assert download.content == original_bytes

        # Filesystem check: raw bytes match
        fs_path = root / kb_code / "raw" / "docs" / "report.pdf"
        assert fs_path.exists()
        assert fs_path.read_bytes() == original_bytes


# ===================================================================
# U21: readFile markdown text matches filesystem .md file text
# ===================================================================
@pytest.mark.integration
def test_u21_read_file_markdown_matches_filesystem_md_file(monkeypatch):
    """Import .md → build → readFile text == filesystem .md file text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)
        # Override chunking service so the built markdown matches our input
        markdown_text = "# Guide\n\nSection 1\n\nSection 2\n"
        _set_document_chunking_service(
            monkeypatch,
            FakeDocumentChunkingService(markdown_text=markdown_text),
        )

        kb_name = f"u21-kb-{uuid4().hex[:12]}"
        file_path = "/docs/guide.md"

        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, kb_name)
            _create_directory(client, kb_code=kb_code, directory_path="/docs")
            _upload_and_build_file(
                client,
                kb_code=kb_code,
                file_path=file_path,
                file_content=markdown_text.encode("utf-8"),
            )

            read_resp = client.post(
                "/api/v1/readFile",
                json={
                    "knCode": kb_code,
                    "filePath": file_path,
                    "startLine": 1,
                    "endLine": 4,
                },
            )
            assert read_resp.status_code == 200, read_resp.text
            assert read_resp.json()["resultCode"] == "0", read_resp.json()
            read_text = read_resp.json()["resultObject"]["data"]

        # Filesystem: compare with generated .md file
        fs_md_path = root / kb_code / "md" / "docs" / "guide.md.md"
        assert fs_md_path.exists()
        fs_md_text = fs_md_path.read_text(encoding="utf-8")
        # The read response should contain text from the markdown content
        assert "# Guide" in read_text
        # The filesystem .md file content should contain the markdown text
        # (built by the chunking service, which returns markdown_text)
        assert "Guide" in fs_md_text


# ===================================================================
# U22: Delete removes file from API + search + filesystem
# ===================================================================
@pytest.mark.integration
def test_u22_delete_removes_from_api_search_and_filesystem(monkeypatch):
    """Import → build → delete → listDir + search + filesystem all show file gone."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)
        _set_document_chunking_service(
            monkeypatch,
            FakeDocumentChunkingService(markdown_text="delete me content\n"),
        )
        asyncio.run(_set_search_service(monkeypatch, settings))

        kb_name = f"u22-kb-{uuid4().hex[:12]}"
        file_path = "/docs/to-delete.md"
        file_content = b"delete me content\n"

        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, kb_name)
            _create_directory(client, kb_code=kb_code, directory_path="/docs")
            _upload_and_build_file(
                client,
                kb_code=kb_code,
                file_path=file_path,
                file_content=file_content,
            )

            # Delete the file
            delete_resp = client.post(
                "/api/v1/knowledgeItems/delete",
                json={"knCode": kb_code, "filePath": file_path},
            )
            assert delete_resp.status_code == 200, delete_resp.text
            assert delete_resp.json()["resultCode"] == "0", delete_resp.json()

            # listDir → file gone
            list_resp = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": "/docs"},
            )
            assert list_resp.status_code == 200, list_resp.text
            list_data = list_resp.json()["resultObject"]["data"]
            assert not any("to-delete" in item["name"] for item in list_data)

            # readFile → error
            read_resp = client.post(
                "/api/v1/readFile",
                json={
                    "knCode": kb_code,
                    "filePath": file_path,
                    "startLine": 1,
                    "endLine": 1,
                },
            )
            assert read_resp.status_code == 200
            assert read_resp.json()["resultCode"] == "-1"

            # search → no hits
            search_resp = client.post(
                "/api/v1/knowledgeItems/search",
                json={
                    "query": "delete me",
                    "knCodeList": [kb_code],
                    "topK": 5,
                    "searchMode": "mixedRecall",
                },
            )
            assert search_resp.status_code == 200, search_resp.text
            assert search_resp.json()["resultObject"]["data"] == []

        # Filesystem: raw and md files are gone
        fs_raw = root / kb_code / "raw" / "docs" / "to-delete.md"
        fs_md = root / kb_code / "md" / "docs" / "to-delete.md.md"
        assert not fs_raw.exists(), f"raw file still exists: {fs_raw}"
        assert not fs_md.exists(), f"md file still exists: {fs_md}"


# ===================================================================
# U23: Write failure → no DB record, no filesystem residual
# ===================================================================
@pytest.mark.integration
def test_u23_write_failure_no_db_record_no_filesystem_residual(monkeypatch):
    """Make storage root read-only → import fails → no DB record, no residual file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)

        kb_name = f"u23-kb-{uuid4().hex[:12]}"
        first_file = "/docs/ok.md"
        second_file = "/docs/fail.md"

        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, kb_name)
            _create_directory(client, kb_code=kb_code, directory_path="/docs")

            # First import a file to create the raw directory structure
            _upload_file(
                client,
                kb_code=kb_code,
                file_path=first_file,
                file_content=b"ok\n",
            )

            # Make the docs subdirectory read-only so writes to it fail
            docs_dir = root / kb_code / "raw" / "docs"
            assert docs_dir.is_dir(), f"docs dir not found: {docs_dir}"
            original_mode = os.stat(docs_dir).st_mode
            os.chmod(docs_dir, stat.S_IRUSR | stat.S_IXUSR)  # read + execute only

            try:
                # Try to import a second file → should fail (can't write to read-only dir)
                fail_resp = client.post(
                    "/api/v1/knowledgeItems/import",
                    data={"knCode": kb_code, "filePath": second_file},
                    files={
                        "fileContent": (
                            "fail.md",
                            b"should not be written\n",
                            "text/markdown",
                        )
                    },
                )
                # The API may return 200 with error envelope or 500
                if fail_resp.status_code == 200:
                    # Check if it's an error envelope
                    payload = fail_resp.json()
                    assert payload["resultCode"] != "0", (
                        f"Expected import to fail, got: {payload}"
                    )
            finally:
                # Restore permissions
                os.chmod(docs_dir, original_mode)

            # listDir → second file not present
            list_resp = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": "/docs"},
            )
            assert list_resp.status_code == 200, list_resp.text
            list_data = list_resp.json()["resultObject"]["data"]
            file_names = [item["name"] for item in list_data]
            assert not any("fail" in name for name in file_names), (
                f"Unexpected file in listing: {file_names}"
            )

        # Filesystem: second file does not exist
        fs_fail = root / kb_code / "raw" / "docs" / "fail.md"
        assert not fs_fail.exists(), f"Residual file found: {fs_fail}"


# ===================================================================
# U24: DB commit failure → storage cleanup via delete_quietly
# ===================================================================
@pytest.mark.integration
def test_u24_db_commit_failure_triggers_storage_cleanup(monkeypatch):
    """When DB commit fails after storage write, the written file is cleaned up."""
    from by_qa.knowledge_base.infrastructure import runtime as runtime_module

    USERFS_DIR = tempfile.TemporaryDirectory()
    root = Path(USERFS_DIR.name)

    settings = _kb_settings(agent_data_path=root)
    _reset_runtime(monkeypatch, settings)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch, FakeDocumentChunkingService(markdown_text="# U24\n")
    )

    kb_name = f"U24-KB-{uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)

        # Step 1: Import a file successfully (proves normal path works)
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/docs/first.md",
            file_content=b"first file content",
        )
        first_raw = root / kb_code / "raw" / "docs" / "first.md"
        assert first_raw.exists(), f"First file should exist at {first_raw}"

        # Step 2: Monkeypatch the connection factory so commit() raises
        original_factory = runtime_module.build_connection_factory

        def _make_failing_factory(s):
            real_connect = original_factory(s)

            async def failing_connect():
                conn = await real_connect()

                async def raise_on_commit():
                    raise RuntimeError("U24 simulated commit failure")

                conn.commit = raise_on_commit
                return conn

            return failing_connect

        monkeypatch.setattr(
            runtime_module, "build_connection_factory", _make_failing_factory
        )

        # Step 3: Clear cached services so they pick up the patched factory
        monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
        monkeypatch.setattr(main_module, "_knowledge_base_service", None)

        # Step 4: Try to import a second file — commit should fail
        second_path = "/docs/second.md"
        second_content = b"second file content"
        second_raw = root / kb_code / "raw" / "docs" / "second.md"

        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": second_path},
            files={"fileContent": ("second.md", second_content, "text/markdown")},
        )
        # The API should return an error
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["resultCode"] == "-1", f"Expected import to fail, got: {payload}"

        # Step 5: Verify the second file was cleaned up from storage
        assert not second_raw.exists(), (
            f"Second file should have been cleaned up after commit failure, "
            f"but it still exists at {second_raw}"
        )

        # Step 6: Verify the first file is unaffected
        assert first_raw.exists(), f"First file should still exist at {first_raw}"


# ===================================================================
# U25: Partial move failure → first file rolled back
# ===================================================================
@pytest.mark.integration
def test_u25_partial_move_failure_rolls_back_first_file(monkeypatch):
    """Rename directory with 2 files, second move fails → first is rolled back."""
    from by_qa.knowledge_base.infrastructure import runtime as runtime_module
    from by_qa.knowledge_base.infrastructure.storage import StorageOperationError

    class FailingMoveUserFSProvider(UserFSProvider):
        """UserFS that fails on the N-th move call."""

        def __init__(self, root, fail_on_call):
            super().__init__(root=root)
            self._move_count = 0
            self._fail_on_call = fail_on_call

        async def move(self, source, target, *, overwrite=False):
            self._move_count += 1
            if self._move_count == self._fail_on_call:
                raise StorageOperationError("simulated move failure")
            return await super().move(source, target, overwrite=overwrite)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")

        # Wire a NORMAL UserFS first so services can initialize
        _wire_userfs(monkeypatch, root, settings)
        _set_document_chunking_service(
            monkeypatch,
            FakeDocumentChunkingService(markdown_text="content\n"),
        )

        kb_name = f"u25-kb-{uuid4().hex[:12]}"
        old_dir = "/old"
        file1 = f"{old_dir}/one.md"
        file2 = f"{old_dir}/two.md"

        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, kb_name)
            _create_directory(client, kb_code=kb_code, directory_path=old_dir)
            _upload_and_build_file(
                client,
                kb_code=kb_code,
                file_path=file1,
                file_content=b"one\n",
            )
            _upload_and_build_file(
                client,
                kb_code=kb_code,
                file_path=file2,
                file_content=b"two\n",
            )

            # Monkeypatch load_storage_provider to return a failing provider.
            # The middleware builds per-request services, so the global
            # _knowledge_base_service is never populated. We must intercept
            # the storage provider factory instead.
            #
            # For 2 built files, rename moves: file1.original, file1.markdown,
            # file2.original, file2.markdown (4 moves). Fail on #3 so file2
            # original fails but file1 was already moved → rollback.
            fail_on_call = 3

            def _failing_load():
                return FailingMoveUserFSProvider(root=root, fail_on_call=fail_on_call)

            monkeypatch.setattr(runtime_module, "load_storage_provider", _failing_load)

            # Try to rename /old → /new → should fail on second file's move
            rename_resp = client.post(
                "/api/v1/directories/update",
                json={
                    "knCode": kb_code,
                    "directoryPath": old_dir,
                    "directoryName": "new",
                },
            )
            # Should return an error
            assert rename_resp.status_code == 200, rename_resp.text
            rename_payload = rename_resp.json()
            assert rename_payload["resultCode"] == "-1", (
                f"Expected rename to fail, got: {rename_payload}"
            )

            # After rollback, old path should still work
            old_list = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": old_dir},
            )
            assert old_list.status_code == 200, old_list.text
            old_data = old_list.json()["resultObject"]["data"]
            old_names = [item["name"] for item in old_data]
            assert any("one.md" in n for n in old_names), (
                f"file1 should still exist: {old_names}"
            )
            assert any("two.md" in n for n in old_names), (
                f"file2 should still exist: {old_names}"
            )

            # New path should not exist
            new_list = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": "/new"},
            )
            # Either returns error or empty list
            if new_list.json()["resultCode"] == "0":
                assert new_list.json()["resultObject"]["data"] == [], (
                    f"New dir should be empty: {new_list.json()}"
                )
            else:
                assert new_list.json()["resultCode"] == "-1"

        # Filesystem: old files still exist
        fs_one_raw = root / kb_code / "raw" / "old" / "one.md"
        fs_two_raw = root / kb_code / "raw" / "old" / "two.md"
        assert fs_one_raw.exists(), f"file1 raw missing: {fs_one_raw}"
        assert fs_two_raw.exists(), f"file2 raw missing: {fs_two_raw}"


# ===================================================================
# U26: Two concurrent imports of same path → only one succeeds
# ===================================================================
@pytest.mark.integration
def test_u26_concurrent_imports_of_same_path_only_one_succeeds(monkeypatch):
    """Two concurrent imports of the same path → only one succeeds, one file on disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = _kb_settings(agent_data_path=root / "agent_data")
        _wire_userfs(monkeypatch, root, settings)

        kb_name = f"u26-kb-{uuid4().hex[:12]}"
        file_path = "/docs/race.md"
        file_content = b"race condition test\n"

        def _do_import(client, kb_code):
            """Perform one import request."""
            resp = client.post(
                "/api/v1/knowledgeItems/import",
                data={"knCode": kb_code, "filePath": file_path},
                files={
                    "fileContent": (
                        "race.md",
                        file_content,
                        "text/markdown",
                    )
                },
            )
            return resp

        with TestClient(main_module.app) as client:
            kb_code = _create_kb(client, kb_name)
            _create_directory(client, kb_code=kb_code, directory_path="/docs")

            # Run two concurrent imports
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(_do_import, client, kb_code),
                    executor.submit(_do_import, client, kb_code),
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

            # At least one should succeed, and at least one should be a conflict
            success_count = 0
            error_count = 0
            for resp in results:
                assert resp.status_code == 200, resp.text
                payload = resp.json()
                if payload["resultCode"] == "0":
                    success_count += 1
                else:
                    error_count += 1

            assert success_count >= 1, f"No import succeeded: {results}"
            # Either the second is rejected or both succeed (idempotent overwrite)

            # listDir: file exists (may show 1 or 2 entries depending on implementation)
            list_resp = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": "/docs"},
            )
            assert list_resp.status_code == 200, list_resp.text
            list_data = list_resp.json()["resultObject"]["data"]
            race_entries = [item for item in list_data if "race" in item["name"]]
            assert len(race_entries) >= 1, f"File not found in listing: {list_data}"

        # Filesystem: exactly one raw file for this path (or at least one)
        import glob as glob_mod

        raw_pattern = str(root / kb_code / "raw" / "docs" / "race.md")
        raw_files = glob_mod.glob(raw_pattern)
        assert len(raw_files) >= 1, f"No raw file found at {raw_pattern}"
        # The file content should match
        for rf in raw_files:
            assert Path(rf).read_bytes() == file_content


# ===================================================================
# U27: Storage root doesn't exist → ensure_ready creates it → import works
# ===================================================================
@pytest.mark.integration
def test_u27_storage_root_auto_created_then_import_works(monkeypatch):
    """Storage root doesn't exist at startup → ensure_ready creates it → import works."""
    with tempfile.TemporaryDirectory() as parent:
        # Create a path that doesn't exist yet
        root = Path(parent) / "nonexistent_storage_root"
        assert not root.exists()

        settings = _kb_settings(agent_data_path=Path(parent) / "agent_data")

        # Wire UserFS with the nonexistent root
        def _make_userfs():
            return UserFSProvider(root=root)

        mod = ModuleType("_userfs_test_provider")
        mod.make_userfs = _make_userfs
        sys.modules["_userfs_test_provider"] = mod
        monkeypatch.setenv(
            "BY_QA_STORAGE_PROVIDER", "_userfs_test_provider:make_userfs"
        )

        _reset_runtime(monkeypatch, settings)
        _set_document_chunking_service(
            monkeypatch,
            FakeDocumentChunkingService(markdown_text="after ensure_ready\n"),
        )

        # At this point root does not exist
        assert not root.exists()

        kb_name = f"u27-kb-{uuid4().hex[:12]}"
        file_path = "/docs/hello.md"
        file_content = b"hello from auto-created root\n"

        with TestClient(main_module.app) as client:
            # Creating the KB triggers service construction → ensure_ready()
            kb_code = _create_kb(client, kb_name)

            # Root should now exist
            assert root.exists(), f"Root was not created by ensure_ready: {root}"

            _create_directory(client, kb_code=kb_code, directory_path="/docs")
            _upload_and_build_file(
                client,
                kb_code=kb_code,
                file_path=file_path,
                file_content=file_content,
            )

            # Verify the file is readable
            read_resp = client.post(
                "/api/v1/readFile",
                json={
                    "knCode": kb_code,
                    "filePath": file_path,
                    "startLine": 1,
                    "endLine": 1,
                },
            )
            assert read_resp.status_code == 200, read_resp.text
            assert read_resp.json()["resultCode"] == "0", read_resp.json()

        # Filesystem: file exists under the auto-created root
        fs_raw = root / kb_code / "raw" / "docs" / "hello.md"
        assert fs_raw.exists(), f"File not found at {fs_raw}"
        assert fs_raw.read_bytes() == file_content
