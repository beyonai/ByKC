"""Real KnowledgeEntity API integration tests.

Unlike the older cross-module integration suite, this module deliberately uses
the application runtime exactly as production wires it.  No service,
repository, database, object-storage, Redis, embedding, or LLM replacement is
allowed here.  The test therefore requires the full integration infrastructure
and actual model endpoints configured through the normal environment/.env.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import get_settings

TERMINAL_TASK_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED"})
TASK_TIMEOUT_SECONDS = 300.0


def _assert_real_runtime_configuration() -> None:
    """Fail fast when the selected real integration runtime is incomplete."""

    settings = get_settings()
    required = {
        "DB_HOST": settings.db_host,
        "DB_USER": settings.db_user,
        "DB_PASS": settings.db_pass,
        "MINIO_ENDPOINT": settings.kb_minio_endpoint,
        "MINIO_ACCESS_KEY": settings.kb_minio_access_key,
        "MINIO_SECRET_KEY": settings.kb_minio_secret_key,
        "EMBEDDING_MODEL_NAME": settings.embedding_model_name,
        "EMBEDDING_BASE_URL": settings.embedding_base_url,
        "EMBEDDING_API_KEY": settings.embedding_api_key,
        "LLM_BASE_URL": settings.llm_base_url,
        "LLM_API_KEY": settings.llm_api_key,
        "LLM_STANDARD_MODEL": settings.llm_standard_model,
        "LLM_LIGHTWEIGHT_MODEL": settings.llm_lightweight_model,
        "REDIS_HOST": settings.redis_host,
        "REDIS_PORT": settings.redis_port,
    }
    missing = [name for name, value in required.items() if value in (None, "", 0)]
    if missing:
        pytest.fail(
            "KnowledgeEntity real integration requires configured external "
            f"dependencies; missing: {', '.join(missing)}",
            pytrace=False,
        )


def _assert_success(response) -> dict:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]


def _create_kb(client: TestClient) -> str:
    result = _assert_success(
        client.post(
            "/api/v1/knowledgeBases/create",
            json={"knName": f"KnowledgeEntity real integration {uuid4().hex}"},
        )
    )
    return result["knCode"]


def _delete_kb(client: TestClient, kb_code: str) -> None:
    response = client.post(
        "/api/v1/knowledgeBases/delete",
        json={"knCode": kb_code},
    )
    _assert_success(response)


def _upload_markdown(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    content: str,
) -> None:
    _assert_success(
        client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": file_path},
            files={
                "fileContent": (
                    file_path.rsplit("/", 1)[-1],
                    content.encode("utf-8"),
                    "text/markdown",
                )
            },
        )
    )


def _update_markdown(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    content: str,
) -> None:
    _assert_success(
        client.post(
            "/api/v1/knowledgeItems/update",
            data={"knCode": kb_code, "filePath": file_path},
            files={
                "fileContent": (
                    file_path.rsplit("/", 1)[-1],
                    content.encode("utf-8"),
                    "text/markdown",
                )
            },
        )
    )


def _build_markdown_index(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
) -> None:
    response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": kb_code, "filePath": file_path},
    )
    _assert_success(response)
    build_status = _assert_success(
        client.post(
            "/api/v1/fileBuildStatus",
            json={"knCode": kb_code, "filePath": file_path},
        )
    )
    assert build_status["status"] == "complete", build_status
    build_result = _assert_success(
        client.post(
            "/api/v1/buildResult",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "includeMarkdown": True,
            },
        )
    )
    assert build_result["build"]["status"] == "complete"
    assert build_result["markdown"]["available"] is True
    assert build_result["markdown"]["data"]
    assert build_result["chunks"]["total"] >= 1
    assert build_result["embedding"]["embeddedChunkCount"] >= 1
    assert build_result["retrieval"]["indexedChunkCount"] >= 1


def _metadata(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str,
    field_names: Iterable[str],
) -> dict:
    return _assert_success(
        client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "metadataFieldList": list(field_names),
            },
        )
    )["metadata"]


def _task_page(
    client: TestClient,
    *,
    kb_code: str,
    file_path: str | None = None,
    batch_id: str | None = None,
    task_type: str | None = None,
    latest_only: bool = False,
) -> dict:
    body: dict[str, object] = {
        "knCode": kb_code,
        "latestOnly": latest_only,
        "includeDetails": True,
        "pageNum": 1,
        "pageSize": 100,
    }
    if file_path is not None:
        body["filePath"] = file_path
    if batch_id is not None:
        body["batchId"] = batch_id
    if task_type is not None:
        body["taskType"] = task_type
    return _assert_success(
        client.post("/api/v1/knowledgeItems/processingTaskStatus", json=body)
    )


def _wait_for_batch(
    client: TestClient,
    *,
    kb_code: str,
    batch_id: str,
    expected_task_ids: Iterable[str],
    timeout_seconds: float = TASK_TIMEOUT_SECONDS,
) -> list[dict]:
    expected = set(expected_task_ids)
    assert expected, "the accepted batch must contain at least one real task"
    deadline = time.monotonic() + timeout_seconds
    latest: list[dict] = []
    while time.monotonic() < deadline:
        page = _task_page(
            client,
            kb_code=kb_code,
            batch_id=batch_id,
            latest_only=False,
        )
        latest = [item for item in page["data"] if item["taskId"] in expected]
        if len(latest) == len(expected) and all(
            item["status"] in TERMINAL_TASK_STATUSES for item in latest
        ):
            failures = [item for item in latest if item["status"] != "SUCCEEDED"]
            assert not failures, failures
            return latest
        time.sleep(2.0)
    pytest.fail(
        f"KnowledgeEntity batch {batch_id} did not finish within "
        f"{timeout_seconds}s; last task state: {latest}"
    )


def _entity_paths_from_tasks(tasks: Iterable[dict]) -> list[str]:
    paths: list[str] = []
    for task in tasks:
        for action in (task.get("result") or {}).get("actions", []):
            path = action.get("filePath")
            if action.get("action") in {"CREATED", "ANCHORED"} and path:
                paths.append(path)
    return list(dict.fromkeys(paths))


@pytest.mark.integration
def test_knowledge_entity_real_api_end_to_end() -> None:
    """Exercise KnowledgeEntity through only real public APIs and dependencies."""

    _assert_real_runtime_configuration()
    source_path = "/documents/zetaknowledge-protocol.md"
    second_source_path = "/documents/atlas-index.md"
    source_v1 = """# ZetaKnowledge Protocol

ZetaKnowledge Protocol is an internal knowledge-governance protocol. It defines
how AtlasIndex records canonical knowledge entities and their aliases.

AtlasIndex is the protocol's canonical entity index. ZetaKnowledge Protocol
depends on AtlasIndex for identity resolution.
"""
    source_v2 = (
        source_v1 + "\nThe protocol is maintained by the Knowledge Platform team.\n"
    )
    second_source = """# AtlasIndex

AtlasIndex is the canonical entity index used by ZetaKnowledge Protocol. It
stores stable entity names and aliases for knowledge-governance workflows.
"""

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client)
        try:
            # Import and update must materialize/preserve the default original
            # document kind without front matter supplied by the caller.
            _upload_markdown(
                client,
                kb_code=kb_code,
                file_path=source_path,
                content=source_v1,
            )
            imported_metadata = _metadata(
                client,
                kb_code=kb_code,
                file_path=source_path,
                field_names=["documentKind", "processingCapabilities"],
            )
            assert imported_metadata["documentKind"]["value"] == "original"

            _update_markdown(
                client,
                kb_code=kb_code,
                file_path=source_path,
                content=source_v2,
            )
            updated_metadata = _metadata(
                client,
                kb_code=kb_code,
                file_path=source_path,
                field_names=["documentKind"],
            )
            assert updated_metadata["documentKind"]["value"] == "original"

            _build_markdown_index(client, kb_code=kb_code, file_path=source_path)
            eligibility = _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/processingEligibility",
                    json={
                        "knCode": kb_code,
                        "filePath": source_path,
                        "capability": "entityDiscovery",
                    },
                )
            )
            assert eligibility["documentKind"] == "original"
            assert eligibility["eligibility"] == "ELIGIBLE_AND_STALE"
            assert eligibility["reasonCode"] == "NEVER_PROCESSED"

            single_batch = _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/entityDiscovery",
                    json={
                        "knCode": kb_code,
                        "filePath": source_path,
                        "maxEntities": 3,
                    },
                )
            )
            assert single_batch["scope"] == "SINGLE_FILE"
            assert single_batch["acceptedCount"] == 1
            single_tasks = _wait_for_batch(
                client,
                kb_code=kb_code,
                batch_id=single_batch["batchId"],
                expected_task_ids=(item["taskId"] for item in single_batch["tasks"]),
            )

            # Querying by KB id + file path must return only that source file and
            # include the durable result written by the asynchronous worker.
            source_status = _task_page(
                client,
                kb_code=kb_code,
                file_path=source_path,
                task_type="ENTITY_DISCOVERY",
                latest_only=True,
            )
            assert source_status["filePath"] == source_path
            assert source_status["total"] == 1
            assert source_status["data"][0]["filePath"] == source_path
            assert source_status["data"][0]["status"] == "SUCCEEDED"
            assert source_status["data"][0]["result"]

            entity_paths = _entity_paths_from_tasks(single_tasks)
            assert entity_paths, single_tasks
            assert all(path.startswith("/KnowledgeEntity/") for path in entity_paths)
            entity_path = entity_paths[0]
            entity_metadata = _metadata(
                client,
                kb_code=kb_code,
                file_path=entity_path,
                field_names=[
                    "documentKind",
                    "processingCapabilities",
                    "entityName",
                    "aliases",
                    "definitionVersion",
                ],
            )
            assert entity_metadata["documentKind"]["value"] == "knowledgeEntity"
            assert "entityEnrich" in entity_metadata["processingCapabilities"]["value"]
            assert entity_metadata["entityName"]["value"]
            assert entity_metadata["definitionVersion"]["value"]

            mention_page = _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/semanticRelations",
                    json={
                        "knCode": kb_code,
                        "filePath": source_path,
                        "direction": "OUTGOING",
                        "relationCodeList": ["MENTIONS"],
                    },
                )
            )
            assert mention_page["total"] >= 1
            assert any(
                item["target"]["filePath"].startswith("/KnowledgeEntity/")
                and item["relationCode"] == "MENTIONS"
                for item in mention_page["data"]
            )

            # An unbuilt second original document and the fresh first document
            # exercise all-KB enumeration, eligibility, exclusion of generated
            # KnowledgeEntity files, and durable task reuse without duplicating
            # an already proven real LLM discovery call.
            _upload_markdown(
                client,
                kb_code=kb_code,
                file_path=second_source_path,
                content=second_source,
            )
            whole_batch = _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/entityDiscovery",
                    json={"knCode": kb_code, "maxEntities": 2},
                )
            )
            assert whole_batch["scope"] == "WHOLE_KB"
            assert whole_batch["eligibleCount"] == 1
            assert whole_batch["acceptedCount"] == 0
            assert whole_batch["reusedCount"] == 1
            assert whole_batch["skippedCount"] >= 1
            assert len(whole_batch["tasks"]) == 1
            assert whole_batch["tasks"][0]["filePath"] == source_path
            assert whole_batch["tasks"][0]["reused"] is True

            enrich_eligibility = _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/processingEligibility",
                    json={
                        "knCode": kb_code,
                        "filePath": entity_path,
                        "capability": "entityEnrich",
                    },
                )
            )
            assert enrich_eligibility["documentKind"] == "knowledgeEntity"
            assert enrich_eligibility["eligibility"] == "ELIGIBLE_AND_STALE"

            enrich_batch = _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/entityEnrich",
                    json={
                        "knCode": kb_code,
                        "filePath": entity_path,
                        "topK": 5,
                    },
                )
            )
            assert enrich_batch["scope"] == "SINGLE_FILE"
            assert enrich_batch["acceptedCount"] == 1
            enrich_tasks = _wait_for_batch(
                client,
                kb_code=kb_code,
                batch_id=enrich_batch["batchId"],
                expected_task_ids=(item["taskId"] for item in enrich_batch["tasks"]),
            )
            assert enrich_tasks[0]["taskType"] == "DOCUMENT_ENRICH"
            assert enrich_tasks[0]["result"]["actions"][0]["action"] == "UPDATED"

            enriched_metadata = _metadata(
                client,
                kb_code=kb_code,
                file_path=entity_path,
                field_names=["documentKind", "entityName", "enrichVersion"],
            )
            assert enriched_metadata["documentKind"]["value"] == "knowledgeEntity"
            assert enriched_metadata["entityName"]["value"]
            assert enriched_metadata["enrichVersion"]["value"] == "ke-enrich/1.0"

            # Enrichment replaces only this entity's generated outgoing edges;
            # the endpoint must remain queryable even when the model emits none.
            enriched_relations = _assert_success(
                client.post(
                    "/api/v1/knowledgeItems/semanticRelations",
                    json={
                        "knCode": kb_code,
                        "filePath": entity_path,
                        "direction": "BOTH",
                    },
                )
            )
            assert enriched_relations["fileId"] == enrich_eligibility["fileId"]
            assert enriched_relations["total"] >= 1
        finally:
            _delete_kb(client, kb_code)
