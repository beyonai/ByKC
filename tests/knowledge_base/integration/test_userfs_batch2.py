"""UserFS storage integration tests — scenarios U10 through U18.

These tests verify that the UserFS storage provider correctly manages physical
files on the local filesystem during import, build, delete, rename and KB-level
lifecycle operations.
"""

# pylint: disable=unused-argument

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from _userfs_provider import UserFSProvider
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings
from by_qa.core.model_config import ModelConfig  # noqa: E402
from by_qa.knowledge_base.infrastructure import runtime as runtime_module

# Per-test root set by _wire_userfs; helpers reference this instead of a global.
_current_userfs_root: Path | None = None

# ---------------------------------------------------------------------------
# Test infrastructure
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


class FakeDocumentChunkingService:
    """Stable knowledge_build double used by cross-module API integration tests."""

    def __init__(self, *, markdown_text: str, embedding: list[float] | None = None):
        self.markdown_text = markdown_text
        self.embedding = embedding or _default_embedding_vector()

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        _ = file_type
        assert isinstance(file_bytes, bytes)
        return self.markdown_text

    def chunk_and_embed(self, file_bytes: bytes, *, filename: str) -> list:
        from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

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


def _kb_settings(*, agent_data_path=None) -> Settings:
    updates = {
        "DB_HOST": os.getenv("DB_HOST", DEFAULT_DB_HOST),
        "DB_PORT": int(os.getenv("DB_PORT", DEFAULT_DB_PORT)),
        "DB_DATABASE": os.getenv("DB_DATABASE", DEFAULT_DB_DATABASE),
        "DB_SCHEMA": os.getenv("DB_SCHEMA", ""),
        "DB_USER": os.getenv("DB_USER", DEFAULT_DB_USER),
        "DB_PASS": os.getenv("DB_PASS", DEFAULT_DB_PASS),
        "MINIO_ENDPOINT": os.getenv("MINIO_ENDPOINT", "127.0.0.1:19000"),
        "MINIO_ACCESS_KEY": os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        "MINIO_SECRET_KEY": os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        "KB_MINIO_BUCKET": os.getenv("KB_MINIO_BUCKET", "knowledge-base"),
        "KB_MINIO_MARKDOWN_BUCKET": os.getenv(
            "KB_MINIO_MARKDOWN_BUCKET", "knowledge-base-markdown"
        ),
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


def _wire_userfs(
    monkeypatch: pytest.MonkeyPatch, root: Path, settings: Settings
) -> None:
    global _current_userfs_root
    _current_userfs_root = root
    monkeypatch.setattr(
        runtime_module,
        "load_storage_provider",
        lambda: UserFSProvider(root=root),
    )
    _reset_runtime(monkeypatch, settings)


def _reset_runtime(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    import asyncio

    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(
        main_module,
        "load_model_config_provider",
        lambda: FakeModelConfigProvider(settings),
    )
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
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


def _create_kb(client: TestClient, kb_name: str) -> str:
    """Create a knowledge base and return the assigned knCode."""
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knName": kb_name},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["knCode"]


def _create_directory(
    client: TestClient,
    *,
    kb_code: str,
    directory_path: str,
) -> None:
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
    """Upload a file via the multipart /api/v1/knowledgeItems/import endpoint."""
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
    """Upload a file and build its markdown index."""
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


def _userfs_original_path(kb_code: str, file_path: str) -> Path:
    """Resolve the expected UserFS original-file path."""
    clean = file_path.strip()
    if not clean.startswith("/"):
        clean = "/" + clean
    return _current_userfs_root / kb_code / "raw" / clean.lstrip("/")


def _userfs_markdown_path(kb_code: str, file_path: str) -> Path:
    """Resolve the expected UserFS markdown path."""
    clean = file_path.strip()
    if not clean.startswith("/"):
        clean = "/" + clean
    return _current_userfs_root / kb_code / "md" / f"{clean.lstrip('/')}.md"


# ---------------------------------------------------------------------------
# U10: Import + build -> delete file
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u10_delete_single_file_removes_raw_and_md_from_filesystem(
    monkeypatch, tmp_path
):
    """Delete one file and verify raw and md files are removed from the local filesystem."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="line1\nline2\n"),
    )

    kb_name = f"U10-KB-{uuid4().hex[:12]}"
    file_path = "/docs/example.md"
    file_content = b"Hello from U10\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=file_content,
        )

        orig = _userfs_original_path(kb_code, file_path)
        md = _userfs_markdown_path(kb_code, file_path)
        assert orig.exists(), f"original file should exist at {orig}"
        assert md.exists(), f"markdown file should exist at {md}"

        # Delete the file
        delete_resp = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["resultCode"] == "0"

        # Filesystem: files gone
        assert not orig.exists(), f"original file should be gone at {orig}"
        assert not md.exists(), f"markdown file should be gone at {md}"

        # API: file gone from listDir
        list_resp = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        assert list_resp.status_code == 200
        list_data = list_resp.json()["resultObject"]["data"]
        assert list_data == []

        # API: readFile fails
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


# ---------------------------------------------------------------------------
# U11: Create /A/B -> import files -> build -> delete /A/B
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u11_delete_directory_removes_all_raw_and_md_files_in_subtree(
    monkeypatch, tmp_path
):
    """Delete /A/B and verify ALL files under raw/A/B/ and md/A/B/ are removed."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="content\n"),
    )

    kb_name = f"U11-KB-{uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/A")
        _create_directory(client, kb_code=kb_code, directory_path="/A/B")

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/B/one.md",
            file_content=b"one\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/B/two.md",
            file_content=b"two\n",
        )

        orig_one = _userfs_original_path(kb_code, "/A/B/one.md")
        orig_two = _userfs_original_path(kb_code, "/A/B/two.md")
        md_one = _userfs_markdown_path(kb_code, "/A/B/one.md")
        md_two = _userfs_markdown_path(kb_code, "/A/B/two.md")

        assert orig_one.exists()
        assert orig_two.exists()
        assert md_one.exists()
        assert md_two.exists()

        # Delete /A/B
        delete_resp = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/A/B"},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["resultCode"] == "0"

        # Filesystem: all files gone
        assert not orig_one.exists(), f"{orig_one} should be gone"
        assert not orig_two.exists(), f"{orig_two} should be gone"
        assert not md_one.exists(), f"{md_one} should be gone"
        assert not md_two.exists(), f"{md_two} should be gone"

        # API: directory gone
        list_resp = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/A"},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["resultObject"]["data"] == []

        deleted_list = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/A/B"},
        )
        assert deleted_list.status_code == 200
        assert deleted_list.json()["resultCode"] == "-1"


# ---------------------------------------------------------------------------
# U12: Create /A/B + /A/C -> import files to both -> delete /A/B
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u12_delete_one_subdir_preserves_sibling_directory_files(monkeypatch, tmp_path):
    """Delete /A/B, verify B files gone from filesystem but C files preserved."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="sibling\n"),
    )

    kb_name = f"U12-KB-{uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/A")
        _create_directory(client, kb_code=kb_code, directory_path="/A/B")
        _create_directory(client, kb_code=kb_code, directory_path="/A/C")

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/B/b_file.md",
            file_content=b"B content\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/C/c_file.md",
            file_content=b"C content\n",
        )

        orig_b = _userfs_original_path(kb_code, "/A/B/b_file.md")
        md_b = _userfs_markdown_path(kb_code, "/A/B/b_file.md")
        orig_c = _userfs_original_path(kb_code, "/A/C/c_file.md")
        md_c = _userfs_markdown_path(kb_code, "/A/C/c_file.md")

        assert orig_b.exists() and orig_c.exists()

        # Delete /A/B
        delete_resp = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/A/B"},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["resultCode"] == "0"

        # Filesystem: B gone, C preserved
        assert not orig_b.exists(), f"{orig_b} should be gone"
        assert not md_b.exists(), f"{md_b} should be gone"
        assert orig_c.exists(), f"{orig_c} should be preserved"
        assert md_c.exists(), f"{md_c} should be preserved"
        assert orig_c.read_bytes() == b"C content\n"

        # API: B listing gone, C listing still has the file
        list_b = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/A/B"},
        )
        assert list_b.status_code == 200
        assert list_b.json()["resultCode"] == "-1"

        list_a = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/A"},
        )
        assert list_a.status_code == 200
        a_data = list_a.json()["resultObject"]["data"]
        assert any("C" in item["name"] for item in a_data)

        list_c = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/A/C"},
        )
        assert list_c.status_code == 200
        c_data = list_c.json()["resultObject"]["data"]
        assert any("c_file.md" in item["name"] for item in c_data)


# ---------------------------------------------------------------------------
# U13: Create KB -> import multiple files -> delete KB
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u13_delete_knowledge_base_removes_entire_kb_code_directory(
    monkeypatch, tmp_path
):
    """Delete a knowledge base and verify {root}/{kb_code}/ is entirely gone from storage."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="kb-delete\n"),
    )

    kb_name = f"U13-KB-{uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/Docs")
        _create_directory(client, kb_code=kb_code, directory_path="/Docs/Sub")

        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Docs/Sub/a.md",
            file_content=b"alpha\n",
        )
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/Docs/Sub/b.md",
            file_content=b"beta\n",
        )

        kb_dir = _current_userfs_root / kb_code
        assert kb_dir.exists(), f"KB directory should exist at {kb_dir}"
        assert _userfs_original_path(kb_code, "/Docs/Sub/a.md").exists()
        assert _userfs_markdown_path(kb_code, "/Docs/Sub/a.md").exists()

        # Delete KB
        delete_resp = client.post(
            "/api/v1/knowledgeBases/delete",
            json={"knCode": kb_code},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["resultCode"] == "0"

        # Filesystem: all files gone, but directory structure may remain
        # (individual files are deleted by delete_quietly)
        assert not _userfs_original_path(kb_code, "/Docs/Sub/a.md").exists()
        assert not _userfs_markdown_path(kb_code, "/Docs/Sub/a.md").exists()
        assert not _userfs_original_path(kb_code, "/Docs/Sub/b.md").exists()
        assert not _userfs_markdown_path(kb_code, "/Docs/Sub/b.md").exists()

        # delete_quietly only removes individual files, not the empty
        # directory hierarchy. Verify that no actual files remain.
        for candidate in [kb_dir / "raw", kb_dir / "md"]:
            if candidate.exists():
                remaining_files = [p for p in candidate.rglob("*") if p.is_file()]
                assert len(remaining_files) == 0, (
                    f"Expected no remaining files in {candidate}, found {remaining_files}"
                )

        # API: KB gone
        list_resp = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/"},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["resultCode"] == "-1"

        read_resp = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/Docs/Sub/a.md",
                "startLine": 1,
                "endLine": 1,
            },
        )
        assert read_resp.status_code == 200
        assert read_resp.json()["resultCode"] == "-1"


# ---------------------------------------------------------------------------
# U14: Create /old -> import -> build -> rename /old -> /new
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u14_rename_directory_migrates_raw_files_on_filesystem(monkeypatch, tmp_path):
    """Rename /old to /new, verify raw file moved on filesystem with same content."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="rename-me\ncontent\n"),
    )

    kb_name = f"U14-KB-{uuid4().hex[:12]}"
    file_content = b"rename-me\ncontent\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/old")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/old/file.md",
            file_content=file_content,
        )

        old_orig = _userfs_original_path(kb_code, "/old/file.md")
        assert old_orig.exists()
        assert old_orig.read_bytes() == file_content

        # Rename /old -> /new
        rename_resp = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/old",
                "directoryName": "new",
            },
        )
        assert rename_resp.status_code == 200, rename_resp.text
        assert rename_resp.json()["resultCode"] == "0"

        # Filesystem: old gone, new exists with same content
        assert not old_orig.exists(), f"old original should be gone: {old_orig}"
        new_orig = _userfs_original_path(kb_code, "/new/file.md")
        assert new_orig.exists(), f"new original should exist: {new_orig}"
        assert new_orig.read_bytes() == file_content


# ---------------------------------------------------------------------------
# U15: Same as U14 but verify markdown path migration
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u15_rename_directory_migrates_markdown_files_and_readfile_works(
    monkeypatch, tmp_path
):
    """Rename /old to /new, verify md file migration and readFile on new path works."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="md-migrate\ncontent\n"),
    )

    kb_name = f"U15-KB-{uuid4().hex[:12]}"
    file_content = b"md-migrate\ncontent\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/old")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/old/file.md",
            file_content=file_content,
        )

        old_md = _userfs_markdown_path(kb_code, "/old/file.md")
        assert old_md.exists()

        # Rename /old -> /new
        rename_resp = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/old",
                "directoryName": "new",
            },
        )
        assert rename_resp.status_code == 200, rename_resp.text
        assert rename_resp.json()["resultCode"] == "0"

        # Filesystem: old md gone, new md exists
        assert not old_md.exists(), f"old md should be gone: {old_md}"
        new_md = _userfs_markdown_path(kb_code, "/new/file.md")
        assert new_md.exists(), f"new md should exist: {new_md}"

        # API: readFile on new path works
        read_resp = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/new/file.md",
                "startLine": 1,
                "endLine": 2,
            },
        )
        assert read_resp.status_code == 200
        assert read_resp.json()["resultCode"] == "0"
        assert read_resp.json()["resultObject"]["data"]

        # API: readFile on old path fails
        old_read = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": "/old/file.md",
                "startLine": 1,
                "endLine": 1,
            },
        )
        assert old_read.status_code == 200
        assert old_read.json()["resultCode"] == "-1"


# ---------------------------------------------------------------------------
# U16: Create /A/B/C -> import -> build -> rename /A/B -> /A/X
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u16_rename_middle_directory_preserves_descendant_paths_in_storage(
    monkeypatch, tmp_path
):
    """Rename /A/B to /A/X, verify A/B/C/ -> A/X/C/ for both raw and md on filesystem."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="deep\nrename\n"),
    )

    kb_name = f"U16-KB-{uuid4().hex[:12]}"
    file_content = b"deep\nrename\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/A")
        _create_directory(client, kb_code=kb_code, directory_path="/A/B")
        _create_directory(client, kb_code=kb_code, directory_path="/A/B/C")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/B/C/file.md",
            file_content=file_content,
        )

        old_orig = _userfs_original_path(kb_code, "/A/B/C/file.md")
        old_md = _userfs_markdown_path(kb_code, "/A/B/C/file.md")
        assert old_orig.exists() and old_md.exists()

        # Rename /A/B -> /A/X
        rename_resp = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/A/B",
                "directoryName": "X",
            },
        )
        assert rename_resp.status_code == 200, rename_resp.text
        assert rename_resp.json()["resultCode"] == "0"

        # Filesystem: old paths gone
        assert not old_orig.exists(), f"old original should be gone: {old_orig}"
        assert not old_md.exists(), f"old markdown should be gone: {old_md}"

        # Filesystem: new paths exist
        new_orig = _userfs_original_path(kb_code, "/A/X/C/file.md")
        new_md = _userfs_markdown_path(kb_code, "/A/X/C/file.md")
        assert new_orig.exists(), f"new original should exist: {new_orig}"
        assert new_md.exists(), f"new markdown should exist: {new_md}"
        assert new_orig.read_bytes() == file_content


# ---------------------------------------------------------------------------
# U17: After rename, verify old path downloadFile fails, new path works
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u17_after_rename_old_download_fails_new_download_returns_content(
    monkeypatch, tmp_path
):
    """After directory rename, downloadFile on old path fails, new path returns content."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="download-after-rename\n"),
    )

    kb_name = f"U17-KB-{uuid4().hex[:12]}"
    file_content = b"download-after-rename\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/src")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/src/data.txt",
            file_content=file_content,
            content_type="text/plain",
        )

        # Rename /src -> /dst
        rename_resp = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/src",
                "directoryName": "dst",
            },
        )
        assert rename_resp.status_code == 200, rename_resp.text
        assert rename_resp.json()["resultCode"] == "0"

        # Old path downloadFile fails
        old_dl = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/src/data.txt"},
        )
        assert old_dl.status_code == 200
        # downloadFile returns HTTP 200 but content may be error message
        # We check that it's not a successful download
        assert (
            old_dl.json()["resultCode"] == "-1"
            if old_dl.headers.get("content-type", "").startswith("application/json")
            else True
        )

        # New path downloadFile returns content
        new_dl = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": "/dst/data.txt"},
        )
        assert new_dl.status_code == 200
        assert new_dl.content == file_content


# ---------------------------------------------------------------------------
# U18: Create /A -> import -> rename A->B -> rename B->C
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_u18_double_rename_removes_intermediate_dirs_and_keeps_final(
    monkeypatch, tmp_path
):
    """Double rename /A -> /B -> /C, verify raw/A/ and raw/B/ gone, raw/C/ exists."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, tmp_path / "userfs", settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="double\nrename\nchain\n"),
    )

    kb_name = f"U18-KB-{uuid4().hex[:12]}"
    file_content = b"double\nrename\nchain\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/A")
        _upload_and_build_file(
            client,
            kb_code=kb_code,
            file_path="/A/doc.md",
            file_content=file_content,
        )

        orig_a = _userfs_original_path(kb_code, "/A/doc.md")
        assert orig_a.exists()

        # First rename: A -> B
        r1 = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/A",
                "directoryName": "B",
            },
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["resultCode"] == "0"

        # Filesystem: A gone, B exists
        assert not orig_a.exists(), f"original under A should be gone: {orig_a}"
        orig_b = _userfs_original_path(kb_code, "/B/doc.md")
        assert orig_b.exists(), f"original under B should exist: {orig_b}"

        # Second rename: B -> C
        r2 = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": kb_code,
                "directoryPath": "/B",
                "directoryName": "C",
            },
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["resultCode"] == "0"

        # Filesystem: B gone, C exists
        assert not orig_b.exists(), f"original under B should be gone: {orig_b}"
        orig_c = _userfs_original_path(kb_code, "/C/doc.md")
        assert orig_c.exists(), f"original under C should exist: {orig_c}"
        assert orig_c.read_bytes() == file_content

        # Also check markdown path follows
        md_a = _userfs_markdown_path(kb_code, "/A/doc.md")
        md_b = _userfs_markdown_path(kb_code, "/B/doc.md")
        md_c = _userfs_markdown_path(kb_code, "/C/doc.md")
        assert not md_a.exists()
        assert not md_b.exists()
        assert md_c.exists()
