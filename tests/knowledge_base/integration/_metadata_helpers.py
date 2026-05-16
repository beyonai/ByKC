"""Shared helpers for metadata integration tests.

Lives next to the test file (not conftest) to avoid leaking fixtures
into other knowledge_base integration tests.

Hits real services: OpenGauss + MinIO + Redis (via `make kb-stack-up`)
plus a real embedding API (configured via EMBEDDING_BASE_URL /
EMBEDDING_API_KEY / EMBEDDING_MODEL_NAME / EMBEDDING_DIMENSION env vars
provided by the test environment).  No fakes / no monkeypatch of the
chunking or embedding service.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from typing import Iterator
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings

# Reused only for non-fake helpers (creating a KB, uploading a file).
from tests.knowledge_base.integration.test_kb_api_stateful_integration import (
    _create_kb,
    _upload_file,
)

# --- Settings -----------------------------------------------------------
# Defaults match scripts/knowledge_base/run_integration_tests.sh and the
# kb-stack docker compose.  Embedding-related env vars come from the
# environment unchanged (no fake fallback).

DB_DEFAULTS = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "15432",
    "DB_DATABASE": "postgres",
    "DB_USER": "gaussdb",
    "DB_PASS": "OpenGauss#2026",
}


def _settings() -> Settings:
    return Settings(
        DB_HOST=os.getenv("DB_HOST", DB_DEFAULTS["DB_HOST"]),
        DB_PORT=int(os.getenv("DB_PORT", DB_DEFAULTS["DB_PORT"])),
        DB_DATABASE=os.getenv("DB_DATABASE", DB_DEFAULTS["DB_DATABASE"]),
        DB_SCHEMA=os.getenv("DB_SCHEMA", ""),
        DB_USER=os.getenv("DB_USER", DB_DEFAULTS["DB_USER"]),
        DB_PASS=os.getenv("DB_PASS", DB_DEFAULTS["DB_PASS"]),
        MINIO_ENDPOINT=os.getenv("MINIO_ENDPOINT", "127.0.0.1:19000"),
        MINIO_ACCESS_KEY=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        MINIO_SECRET_KEY=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        KB_MINIO_BUCKET=os.getenv("KB_MINIO_BUCKET", "knowledge-base"),
        KB_MINIO_MARKDOWN_BUCKET=os.getenv(
            "KB_MINIO_MARKDOWN_BUCKET", "knowledge-base-markdown"
        ),
        MINIO_SECURE=False,
        # Embedding settings come straight from env; no fake defaults.
        EMBEDDING_MODEL_NAME=os.environ["EMBEDDING_MODEL_NAME"],
        EMBEDDING_BASE_URL=os.environ["EMBEDDING_BASE_URL"],
        EMBEDDING_API_KEY=os.environ["EMBEDDING_API_KEY"],
        EMBEDDING_DIMENSION=int(os.environ["EMBEDDING_DIMENSION"]),
        EMBEDDING_DISTANCE_METRIC=os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine"),
    )


def _reset_runtime(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Reset by_qa.main module-level singletons so every test starts clean.

    No service-level fakes are installed.  The lifespan hook in TestClient
    builds the real services against the docker stack + embedding API.
    """
    monkeypatch.setattr(main_module, "settings", settings)
    for attr in (
        "_knowledge_base_service",
        "_knowledge_item_ingestion_service",
        "_knowledge_item_search_service",
        "_knowledge_fetch_cache_cleanup_service",
        "_document_chunking_service",
        "_metadata_property_service",
        "_file_metadata_service",
        "_metadata_search_service",
    ):
        monkeypatch.setattr(main_module, attr, None)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_initialized", False)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_lock", asyncio.Lock())

    # Service-registry side-effects depend on Redis; the existing test
    # suite stubs them out to keep the test focused on KB behavior.
    async def _noop(application):  # pylint: disable=unused-argument
        return None

    monkeypatch.setattr(main_module, "_register_service", _noop)
    monkeypatch.setattr(main_module, "_unregister_service", _noop)


@contextlib.contextmanager
def runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Reset runtime + return a TestClient hitting real services."""
    settings = _settings()
    _reset_runtime(monkeypatch, settings)
    with TestClient(main_module.app) as client:
        yield client


# --- Property registration helpers --------------------------------------


def register_property(
    client: TestClient,
    name: str,
    value_type: str,
    *,
    description: str | None = None,
) -> None:
    body = {"propertyName": name, "valueType": value_type}
    if description:
        body["description"] = description
    resp = client.post("/api/v1/metadataProperties/create", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["resultCode"] == "0", resp.text


@dataclass(frozen=True)
class PropSet:
    """Stable property names used by the DSL dataset."""

    status: str
    priority: str
    tags: str
    archived: str
    published_at: str


def register_property_set(client: TestClient) -> PropSet:
    suffix = uuid4().hex[:6]
    ps = PropSet(
        status=f"status_{suffix}",
        priority=f"priority_{suffix}",
        tags=f"tags_{suffix}",
        archived=f"archived_{suffix}",
        published_at=f"publishedAt_{suffix}",
    )
    register_property(client, ps.status, "string")
    register_property(client, ps.priority, "number")
    register_property(client, ps.tags, "stringList")
    register_property(client, ps.archived, "boolean")
    register_property(client, ps.published_at, "datetime")
    return ps


DEFAULT_PROP_SET = PropSet(
    status="status",
    priority="priority",
    tags="tags",
    archived="archived",
    published_at="publishedAt",
)


# --- KB / file factories ------------------------------------------------


def new_kb(client: TestClient) -> str:
    return _create_kb(client, f"MetaKB_{uuid4().hex[:8]}")


def _create_dir(client: TestClient, kb_code: str, path: str) -> None:
    resp = client.post(
        "/api/v1/directories/create",
        json={"knCode": kb_code, "directoryPath": path},
    )
    assert resp.status_code == 200, resp.text


def new_kb_with_file(
    client: TestClient,
    *,
    file_path: str = "/docs/test.md",
    content: bytes = b"# Test\nbody.",
) -> tuple[str, str]:
    kb_code = new_kb(client)
    parent = file_path.rsplit("/", 1)[0]
    if parent:
        _create_dir(client, kb_code, parent)
    _upload_file(client, kb_code=kb_code, file_path=file_path, file_content=content)
    return kb_code, file_path


def new_kb_with_built_file(
    client: TestClient,
    *,
    file_path: str = "/制度/续签流程.md",
    markdown: str = "# 续签流程\n合同续签需由业务负责人发起审批。\n",
) -> tuple[str, str]:
    """Create a KB, upload one markdown file, and run fileToMarkdownIndex.

    Hits the real DocumentChunkingService + embedding API.  Failures here
    indicate the embedding service is unreachable or misconfigured —
    surface them; do not silently fake them.
    """
    kb_code = new_kb(client)
    parent = file_path.rsplit("/", 1)[0]
    if parent:
        _create_dir(client, kb_code, parent)
    _upload_file(
        client,
        kb_code=kb_code,
        file_path=file_path,
        file_content=markdown.encode("utf-8"),
    )
    resp = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": kb_code, "filePath": file_path},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["resultCode"] == "0", resp.text
    return kb_code, file_path


# --- Shared call wrappers -----------------------------------------------
# Used by Section 3+ to keep call sites compact.


def set_metadata(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    property_name: str,
    value=None,
    operation: str = "set",
) -> httpx.Response:
    op: dict = {"propertyName": property_name, "operation": operation}
    if value is not None:
        op["value"] = value
    resp = client.post(
        "/api/v1/knowledgeItems/metadata/update",
        json={"knCode": kb_code, "filePath": file_path, "operationList": [op]},
    )
    assert resp.status_code == 200, resp.text
    return resp


def metadata_search_paths(
    client: TestClient,
    *,
    kb_code: str,
    where: dict,
    top_k: int = 20,
    metadata_field_list: list[str] | None = None,
) -> list[str]:
    body = {"knCodeList": [kb_code], "where": where, "topK": top_k}
    if metadata_field_list is not None:
        body["metadataFieldList"] = metadata_field_list
    resp = client.post("/api/v1/knowledgeItems/metadataSearch", json=body)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["resultCode"] == "0", payload
    return [h["filePath"] for h in payload["resultObject"]["data"]]


def chunk_search(
    client: TestClient,
    *,
    kb_code: str,
    query: str,
    top_k: int = 5,
    mode: str = "mixedRecall",
    where: dict | None = None,
    metadata_field_list: list[str] | None = None,
    file_type_list: list[str] | None = None,
) -> httpx.Response:
    body = {
        "query": query,
        "knCodeList": [kb_code],
        "topK": top_k,
        "searchMode": mode,
    }
    if where is not None:
        body["where"] = where
    if metadata_field_list is not None:
        body["metadataFieldList"] = metadata_field_list
    if file_type_list is not None:
        body["fileTypeList"] = file_type_list
    return client.post("/api/v1/knowledgeItems/search", json=body)


# --- DSL dataset --------------------------------------------------------


@dataclass
class DslDataset:
    kb_code: str
    props: PropSet
    files: dict[str, str]  # logical name -> filePath


def build_dsl_dataset(client: TestClient) -> DslDataset:
    """Create one KB with 6 files and a registered PropSet.

    File matrix (used to assert all DSL operators):
                status     priority  tags                archived  publishedAt
    F1.md       active     1         [hr]                false     2026-01-01T00:00:00Z
    F2.md       active     5         [hr,contract]       false     2026-03-01T00:00:00Z
    F3.md       pending    5         [contract]          true      2026-05-01T00:00:00Z
    F4.md       archived   9         [legal]             true      2025-06-01T00:00:00Z
    F5.pdf      active     3         [hr,legal]          false     (unset)
    F6.md       (no metadata)
    """
    kb_code = new_kb(client)
    _create_dir(client, kb_code, "/dsl")
    files: dict[str, str] = {}
    for name, ext in [
        ("F1", "md"),
        ("F2", "md"),
        ("F3", "md"),
        ("F4", "md"),
        ("F5", "pdf"),
        ("F6", "md"),
    ]:
        path = f"/dsl/{name}.{ext}"
        _upload_file(
            client, kb_code=kb_code, file_path=path, file_content=f"# {name}\n".encode()
        )
        files[name] = path

    ps = register_property_set(client)

    # Inline `_set` is stricter than the public `set_metadata`: it accepts a
    # multi-op list (needed to seed the dataset atomically) and asserts the
    # resultCode envelope so a malformed seed fails fast.
    def _set(file_path: str, ops: list[dict]) -> None:
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={"knCode": kb_code, "filePath": file_path, "operationList": ops},
        )
        assert resp.status_code == 200 and resp.json()["resultCode"] == "0", resp.text

    _set(
        files["F1"],
        [
            {"propertyName": ps.status, "operation": "set", "value": "active"},
            {"propertyName": ps.priority, "operation": "set", "value": 1},
            {"propertyName": ps.tags, "operation": "set", "value": ["hr"]},
            {"propertyName": ps.archived, "operation": "set", "value": False},
            {
                "propertyName": ps.published_at,
                "operation": "set",
                "value": "2026-01-01T00:00:00Z",
            },
        ],
    )
    _set(
        files["F2"],
        [
            {"propertyName": ps.status, "operation": "set", "value": "active"},
            {"propertyName": ps.priority, "operation": "set", "value": 5},
            {"propertyName": ps.tags, "operation": "set", "value": ["hr", "contract"]},
            {"propertyName": ps.archived, "operation": "set", "value": False},
            {
                "propertyName": ps.published_at,
                "operation": "set",
                "value": "2026-03-01T00:00:00Z",
            },
        ],
    )
    _set(
        files["F3"],
        [
            {"propertyName": ps.status, "operation": "set", "value": "pending"},
            {"propertyName": ps.priority, "operation": "set", "value": 5},
            {"propertyName": ps.tags, "operation": "set", "value": ["contract"]},
            {"propertyName": ps.archived, "operation": "set", "value": True},
            {
                "propertyName": ps.published_at,
                "operation": "set",
                "value": "2026-05-01T00:00:00Z",
            },
        ],
    )
    _set(
        files["F4"],
        [
            {"propertyName": ps.status, "operation": "set", "value": "archived"},
            {"propertyName": ps.priority, "operation": "set", "value": 9},
            {"propertyName": ps.tags, "operation": "set", "value": ["legal"]},
            {"propertyName": ps.archived, "operation": "set", "value": True},
            {
                "propertyName": ps.published_at,
                "operation": "set",
                "value": "2025-06-01T00:00:00Z",
            },
        ],
    )
    _set(
        files["F5"],
        [
            {"propertyName": ps.status, "operation": "set", "value": "active"},
            {"propertyName": ps.priority, "operation": "set", "value": 3},
            {"propertyName": ps.tags, "operation": "set", "value": ["hr", "legal"]},
            {"propertyName": ps.archived, "operation": "set", "value": False},
        ],
    )
    return DslDataset(kb_code=kb_code, props=ps, files=files)
