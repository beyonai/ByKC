"""File-count integration tests against the local knowledge middleware stack."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_kb_api_stateful_integration import (
    _create_directory,
    _create_kb,
    _kb_settings,
    _reset_runtime,
    _update_file,
    _update_timeline_count,
    _upload_file,
)

import by_qa.main as main_module
from by_qa.knowledge_base.infrastructure.database import build_connection_factory


def _file_count(client: TestClient, *, kb_code: str) -> int:
    response = client.post("/api/v1/knowledgeBases/fileCount", json={"knCode": kb_code})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resultCode"] == "0", payload
    return payload["resultObject"]["fileCount"]


@pytest.mark.integration
async def test_file_count_tracks_file_lifecycle_and_isolates_knowledge_bases(
    monkeypatch, tmp_path
):
    """Count files across depths, updates, moves and soft deletion in a real DB."""
    from by_qa.knowledge_base.services.markdown_update_summary_service import (
        MarkdownUpdateSummaryService,
    )

    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    monkeypatch.setattr(
        MarkdownUpdateSummaryService,
        "generate_llm_summary",
        AsyncMock(return_value=None),
    )
    with TestClient(main_module.app) as client:
        kb_codes = []
        try:
            kb_code = _create_kb(client, f"File count {uuid4().hex[:12]}")
            kb_codes.append(kb_code)
            other_kb = _create_kb(client, f"Other file count {uuid4().hex[:12]}")
            kb_codes.append(other_kb)
            assert _file_count(client, kb_code=kb_code) == 0
            _create_directory(
                client, kb_code=kb_code, directory_path="/docs/deep/empty"
            )
            assert _file_count(client, kb_code=kb_code) == 0
            _upload_file(
                client,
                kb_code=other_kb,
                file_path="/other.md",
                file_content=b"# Other\n",
            )
            assert _file_count(client, kb_code=other_kb) == 1
            assert _file_count(client, kb_code=kb_code) == 0

            for path in ("/root.md", "/docs/deep/nested.md"):
                _upload_file(
                    client, kb_code=kb_code, file_path=path, file_content=path.encode()
                )
            assert _file_count(client, kb_code=kb_code) == 2

            updated = _update_file(
                client,
                kb_code=kb_code,
                file_path="/root.md",
                file_content=b"# Updated root\n",
            )
            assert updated.json()["resultCode"] == "0", updated.text
            assert (
                await _update_timeline_count(
                    settings, kb_code=kb_code, file_path="/root.md"
                )
                == 1
            )
            assert _file_count(client, kb_code=kb_code) == 2

            moved = client.post(
                "/api/v1/knowledgeItems/move",
                json={
                    "knCode": kb_code,
                    "sourcePath": ["/root.md"],
                    "targetFilePath": "/archive/renamed.md",
                },
            )
            assert moved.json()["resultObject"]["summary"]["succeeded"] == 1
            assert _file_count(client, kb_code=kb_code) == 2
            for endpoint, body, expected_count in (
                ("knowledgeItems/delete", {"filePath": "/archive/renamed.md"}, 1),
                ("directories/delete", {"directoryPath": "/docs"}, 0),
            ):
                deleted = client.post(
                    f"/api/v1/{endpoint}", json={"knCode": kb_code, **body}
                )
                assert deleted.json()["resultCode"] == "0", deleted.text
                assert _file_count(client, kb_code=kb_code) == expected_count
            assert _file_count(client, kb_code=other_kb) == 1

            deleted = client.post(
                "/api/v1/knowledgeBases/delete", json={"knCode": kb_code}
            )
            assert deleted.json()["resultCode"] == "0", deleted.text
            for missing_code in (kb_code, "-1", "not-a-knowledge-base"):
                missing = client.post(
                    "/api/v1/knowledgeBases/fileCount", json={"knCode": missing_code}
                )
                assert missing.status_code == 422
                assert missing.json() == {
                    "resultCode": "-1",
                    "resultMsg": f"knowledge base not found: {missing_code}",
                    "resultObject": {},
                }
        finally:
            for code in kb_codes:
                client.post("/api/v1/knowledgeBases/delete", json={"knCode": code})


@pytest.mark.integration
async def test_file_count_ignores_build_state_and_task_history(monkeypatch, tmp_path):
    """Count files with no task, running/failed tasks, or multiple historical tasks."""
    settings = _kb_settings(agent_data_path=tmp_path)
    _reset_runtime(monkeypatch, settings)
    states = {
        "unbuilt.md": [],
        "running.md": ["running"],
        "failed.md": ["failed", "failed"],
        "succeeded.md": ["failed", "succeeded"],
    }
    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"File count states {uuid4().hex[:12]}")
        try:
            for name in states:
                _upload_file(
                    client,
                    kb_code=kb_code,
                    file_path=f"/states/{name}",
                    file_content=name.encode(),
                )
            connection = await build_connection_factory(settings)()
            try:
                cursor = connection.cursor()
                for name, statuses in states.items():
                    for status in statuses:
                        await cursor.execute(
                            """
                            INSERT INTO knowledge_build_task (
                                knowledge_base_id, fs_entry_id, status, current_step,
                                origin, execution_mode, file_path_snapshot,
                                input_checksum, input_is_deleted, build_profile,
                                build_profile_hash, current_stage
                            )
                            SELECT knowledge_base_id, kid, %(status)s::varchar(32),
                                   'markdown', 'API', 'INLINE', virtual_path, checksum,
                                   false, '{"legacy":true}'::jsonb, repeat('0', 64),
                                   'extracting'
                            FROM knowledge_fs_entry
                            WHERE knowledge_base_id = %(kb_code)s::bigint
                              AND virtual_path = %(path)s AND is_deleted = false
                            """,
                            {
                                "kb_code": kb_code,
                                "path": f"/states/{name}",
                                "status": status,
                            },
                        )
                await connection.commit()
            finally:
                await connection.close()
            assert _file_count(client, kb_code=kb_code) == 4
        finally:
            client.post("/api/v1/knowledgeBases/delete", json={"knCode": kb_code})
