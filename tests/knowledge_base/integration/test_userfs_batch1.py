"""UserFS storage integration tests — scenarios U1-U9.

Verifies that UserFS (local filesystem-backed storage provider) correctly
persists original and markdown files to the expected filesystem paths.

Tests must NOT require docker/redis/MinIO — test in-process.
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
from by_qa.knowledge_base.infrastructure import runtime as runtime_module

# ── Test fixtures & helpers ───────────────────────────────────────────

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
        assert isinstance(file_bytes, bytes)
        return self.markdown_text

    def chunk_and_embed(self, file_bytes: bytes, *, filename: str) -> list:
        from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

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


class PassthroughChunkingService:
    """Chunking service that returns raw file bytes as the markdown text."""

    def __init__(self, embedding: list[float] | None = None):
        self.embedding = embedding or _default_embedding_vector()

    def extract_text_from_file(self, file_bytes: bytes, file_type: str) -> str:
        assert isinstance(file_bytes, bytes)
        return file_bytes.decode("utf-8")

    def chunk_and_embed(self, file_bytes: bytes, *, filename: str) -> list:
        from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

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


class _FakeModelConfigProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def get_config(self, model_type: str):
        from by_qa.core.model_config import ModelConfig

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


# ── Runtime helpers ────────────────────────────────────────────────────


def _wire_userfs(
    monkeypatch: pytest.MonkeyPatch, root: Path, settings: Settings
) -> None:
    """Wire UserFS provider and reset runtime state for one test."""
    monkeypatch.setattr(
        runtime_module,
        "load_storage_provider",
        lambda: UserFSProvider(root=root),
    )
    _reset_runtime(monkeypatch, settings)


def _reset_runtime(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(
        main_module,
        "load_model_config_provider",
        lambda: _FakeModelConfigProvider(settings),
    )
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_initialized", False)
    monkeypatch.setattr(
        main_module, "_knowledge_base_schema_lock", __import__("asyncio").Lock()
    )

    async def _noop_register(application):
        return None

    monkeypatch.setattr(main_module, "_register_service", _noop_register)
    monkeypatch.setattr(main_module, "_unregister_service", _noop_register)


def _set_document_chunking_service(
    monkeypatch: pytest.MonkeyPatch,
    service: FakeDocumentChunkingService | PassthroughChunkingService,
) -> None:
    async def get_service(provider=None):
        return service

    monkeypatch.setattr(
        main_module, "_get_or_build_document_chunking_service", get_service
    )


# ── API helpers ───────────────────────────────────────────────────────


def _create_kb(client: TestClient, kb_name: str) -> str:
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
    response = client.post(
        "/api/v1/knowledgeItems/import",
        data={"knCode": kb_code, "filePath": file_path},
        files={"fileContent": (file_path.split("/")[-1], file_content, content_type)},
    )
    assert response.status_code == 200, response.text


# ═══════════════════════════════════════════════════════════════════════
# U1: Import file → verify filesystem path and content
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u1_import_file_filesystem_path_and_content_match(monkeypatch, tmp_path):
    """U1: Import file, verify {root}/{kb_code}/raw/docs/readme.md exists and content matches."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# U1 Test\n"),
    )

    kb_name = f"U1-KB-{uuid4().hex[:12]}"
    file_path = "/docs/readme.md"
    original_content = b"Hello U1 from UserFS"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/docs")

        _upload_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=original_content,
        )

        expected_path = root / kb_code / "raw" / "docs" / "readme.md"
        assert expected_path.exists(), f"Expected file at {expected_path}"
        assert expected_path.read_bytes() == original_content

        list_resp = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["resultObject"]["data"]
        names = [item["name"] for item in data]
        assert any("readme.md" in n for n in names), f"File not visible: {names}"


# ═══════════════════════════════════════════════════════════════════════
# U2: Import + build → verify markdown path and readFile
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u2_import_and_build_markdown_path_and_readfile(monkeypatch, tmp_path):
    """U2: Import + build, verify markdown file path and readFile returns content."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="U2 line1\nU2 line2\nU2 line3\n"),
    )

    kb_name = f"U2-KB-{uuid4().hex[:12]}"
    file_path = "/docs/readme.md"
    original_content = b"# Some markdown\n\ncontent here\n"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_file(
            client, kb_code=kb_code, file_path=file_path, file_content=original_content
        )

        resp = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["resultCode"] == "0", f"Build failed: {payload}"

        md_path = root / kb_code / "md" / "docs" / "readme.md.md"
        assert md_path.exists(), f"Expected markdown file at {md_path}"

        read_resp = client.post(
            "/api/v1/readFile",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert read_resp.status_code == 200, read_resp.text
        rp = read_resp.json()
        assert rp["resultCode"] == "0", rp
        assert "U2 line1" in rp["resultObject"]["data"]


# ═══════════════════════════════════════════════════════════════════════
# U3: Chinese filename → path preserves characters
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u3_import_chinese_filename_preserves_path_and_content(monkeypatch, tmp_path):
    """U3: Chinese filename /docs/中文文件.md preserves path and works with readFile."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# 中文测试\n\n内容行1\n内容行2\n"),
    )

    kb_name = f"U3-KB-{uuid4().hex[:12]}"
    file_path = "/docs/中文文件.md"
    original_content = "# 你好世界\n".encode("utf-8")

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_file(
            client, kb_code=kb_code, file_path=file_path, file_content=original_content
        )

        expected_path = root / kb_code / "raw" / "docs" / "中文文件.md"
        assert expected_path.exists(), f"Expected file at {expected_path}"
        assert expected_path.read_bytes() == original_content

        list_resp = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["resultObject"]["data"]
        names = [item["name"] for item in data]
        assert any("中文文件.md" in n for n in names), f"File not visible: {names}"


# ═══════════════════════════════════════════════════════════════════════
# U4: File without extension → no dangling dot in path
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u4_import_file_without_extension_no_dangling_dot(monkeypatch, tmp_path):
    """U4: Import file without extension, verify path has no dangling dot."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# README\n\ncontent\n"),
    )

    kb_name = f"U4-KB-{uuid4().hex[:12]}"
    file_path = "/docs/README"
    original_content = b"README with no extension"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_file(
            client, kb_code=kb_code, file_path=file_path, file_content=original_content
        )

        expected_path = root / kb_code / "raw" / "docs" / "README"
        assert expected_path.exists(), f"Expected file at {expected_path}"
        assert expected_path.read_bytes() == original_content

        list_resp = client.post(
            "/api/v1/listDir",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["resultObject"]["data"]
        names = [item["name"] for item in data]
        assert any("README" in n for n in names), f"File not visible: {names}"


# ═══════════════════════════════════════════════════════════════════════
# U5: downloadFile returns bytes matching filesystem content
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u5_download_file_matches_filesystem_content(monkeypatch, tmp_path):
    """U5: downloadFile returns bytes identical to filesystem file content."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# PDF\n\nbinary content\n"),
    )

    kb_name = f"U5-KB-{uuid4().hex[:12]}"
    file_path = "/docs/handbook.pdf"
    original_content = b"%PDF-1.4 fake pdf content here"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path=file_path,
            file_content=original_content,
            content_type="application/pdf",
        )

        fs_file = root / kb_code / "raw" / "docs" / "handbook.pdf"
        assert fs_file.exists()
        assert fs_file.read_bytes() == original_content

        dl_resp = client.post(
            "/api/v1/downloadFile",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert dl_resp.status_code == 200, dl_resp.text
        dl_bytes = dl_resp.content
        assert dl_bytes == original_content, (
            f"downloadFile returned {len(dl_bytes)} bytes, expected {len(original_content)}"
        )


# ═══════════════════════════════════════════════════════════════════════
# U6: readFile with line window matches filesystem markdown
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u6_readfile_line_window_matches_markdown_filesystem(monkeypatch, tmp_path):
    """U6: Build then readFile with line window, verify response matches md file."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(
            markdown_text="L1-alpha\nL2-beta\nL3-gamma\nL4-delta\nL5-epsilon\n"
        ),
    )

    kb_name = f"U6-KB-{uuid4().hex[:12]}"
    file_path = "/docs/readme.md"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/docs")
        _upload_file(
            client, kb_code=kb_code, file_path=file_path, file_content=b"ignored"
        )

        client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": file_path},
        )

        resp = client.post(
            "/api/v1/readFile",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "startLine": 2,
                "endLine": 4,
            },
        )
        assert resp.status_code == 200, resp.text
        rp = resp.json()
        assert rp["resultCode"] == "0", rp
        data = rp["resultObject"]["data"]
        assert "L2-beta" in data
        assert "L3-gamma" in data


# ═══════════════════════════════════════════════════════════════════════
# U7: Deep nested directory tree → correct filesystem paths
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u7_deep_nested_directories_filesystem_paths(monkeypatch, tmp_path):
    """U7: Create /A/B/C → import file → verify deep filesystem paths."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="U7 deep content\nline 2\n"),
    )

    kb_name = f"U7-KB-{uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/A")
        _create_directory(client, kb_code=kb_code, directory_path="/A/B")
        _create_directory(client, kb_code=kb_code, directory_path="/A/B/C")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/A/B/C/file.md",
            file_content=b"deep content",
        )

        expected_raw = root / kb_code / "raw" / "A" / "B" / "C" / "file.md"
        assert expected_raw.exists(), f"Expected raw file at {expected_raw}"

        resp = client.post(
            "/api/v1/fileToMarkdownIndex",
            json={"knCode": kb_code, "filePath": "/A/B/C/file.md"},
        )
        assert resp.status_code == 200, resp.text

        expected_md = root / kb_code / "md" / "A" / "B" / "C" / "file.md.md"
        assert expected_md.exists(), f"Expected md file at {expected_md}"

        for path in ("/A", "/A/B", "/A/B/C"):
            lr = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": path},
            )
            assert lr.status_code == 200, lr.text


# ═══════════════════════════════════════════════════════════════════════
# U8: Sibling directory isolation → paths and content independent
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u8_sibling_directory_path_isolation_and_content_independence(
    monkeypatch, tmp_path
):
    """U8: Same filename in different directories, verify path and content isolation."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(monkeypatch, PassthroughChunkingService())

    kb_name = f"U8-KB-{uuid4().hex[:12]}"

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, kb_name)
        _create_directory(client, kb_code=kb_code, directory_path="/dir1")
        _create_directory(client, kb_code=kb_code, directory_path="/dir2")
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/dir1/readme.md",
            file_content=b"dir1 content",
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path="/dir2/readme.md",
            file_content=b"dir2 content",
        )

        f1 = root / kb_code / "raw" / "dir1" / "readme.md"
        f2 = root / kb_code / "raw" / "dir2" / "readme.md"
        assert f1.read_bytes() == b"dir1 content"
        assert f2.read_bytes() == b"dir2 content"

        for d in ("/dir1", "/dir2"):
            lr = client.post(
                "/api/v1/listDir",
                json={"knCode": kb_code, "directoryPath": d},
            )
            assert lr.status_code == 200
            data = lr.json()["resultObject"]["data"]
            names = [item["name"] for item in data]
            assert any("readme.md" in n for n in names), f"{d}: {names}"


# ═══════════════════════════════════════════════════════════════════════
# U9: Separate KBs same file path → isolated storages
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_u9_separate_kbs_same_path_different_content(monkeypatch, tmp_path):
    """U9: Two KBs with same /readme.md path, verify separate filesystem paths."""
    root = tmp_path / "userfs"
    root.mkdir()

    settings = _kb_settings(agent_data_path=tmp_path)
    _wire_userfs(monkeypatch, root, settings)
    _set_document_chunking_service(monkeypatch, PassthroughChunkingService())

    with TestClient(main_module.app) as client:
        kb1 = _create_kb(client, f"U9-KB1-{uuid4().hex[:12]}")
        kb2 = _create_kb(client, f"U9-KB2-{uuid4().hex[:12]}")
        _upload_file(
            client, kb_code=kb1, file_path="/readme.md", file_content=b"kb1-content"
        )
        _upload_file(
            client, kb_code=kb2, file_path="/readme.md", file_content=b"kb2-content"
        )

        f1 = root / kb1 / "raw" / "readme.md"
        f2 = root / kb2 / "raw" / "readme.md"
        assert f1.exists() and f2.exists()
        assert f1.read_bytes() == b"kb1-content"
        assert f2.read_bytes() == b"kb2-content"
