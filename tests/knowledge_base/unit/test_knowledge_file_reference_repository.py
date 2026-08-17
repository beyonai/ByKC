"""Unit tests for unified relation-assertion persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from by_qa.knowledge_base.repositories.knowledge_file_reference_repository import (
    KnowledgeFileReferenceRepository,
)

HISTORICAL_SCHEMA_PATH = Path(
    "src/by_qa/knowledge_base/sql/026_knowledge_file_reference.sql"
)
ASSERTION_MIGRATION_PATH = Path(
    "src/by_qa/knowledge_base/sql/030_knowledge_file_reference_semantic_extension.sql"
)


class FakeCursor:
    def __init__(self, *, fetchone_results=None, fetchall_results=None):
        self.executed: list[tuple[str, dict | None]] = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchone(self):
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    async def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []


async def test_upsert_relation_assertion_persists_exact_evidence_and_stable_locator():
    row = {"kid": 51, "relation_code": "MENTIONS"}
    cursor = FakeCursor(fetchone_results=[row])
    repo = KnowledgeFileReferenceRepository()

    result = await repo.upsert_relation_assertion(
        cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        target_fs_entry_id=12,
        relation_code="mentions",
        original_target="../entities/Foo.md",
        discovered_by="markdown_parser",
        producer_run_id="rewrite-7",
        evidence_fingerprint="sha256:offset-19",
        start_offset=19,
        end_offset=37,
        target_locator_type="KB_PATH",
        target_locator_value="/KnowledgeEntity/Foo.md",
    )

    assert result == row
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert "INSERT INTO knowledge_file_reference" in normalized
    assert "ON DUPLICATE KEY UPDATE" in normalized
    assert "reference_type" not in normalized
    assert "source.knowledge_base_id = %(knowledge_base_id)s" in normalized
    assert "target.knowledge_base_id = %(knowledge_base_id)s" in normalized
    assert params["relation_code"] == "MENTIONS"
    assert params["discovered_by"] == "MARKDOWN_PARSER"
    assert params["producer_run_id"] == "rewrite-7"
    assert params["evidence_fingerprint"] == "sha256:offset-19"
    assert params["target_locator_type"] == "KB_PATH"
    assert params["target_locator_value"] == "/KnowledgeEntity/Foo.md"


async def test_create_reference_is_a_mentions_assertion_adapter():
    cursor = FakeCursor(fetchone_results=[{"kid": 21}])
    repo = KnowledgeFileReferenceRepository()

    await repo.create_reference(
        cursor,
        knowledge_base_id=1,
        source_fs_entry_id=5,
        target_fs_entry_id=None,
        original_target="missing.md#intro",
        target_path="/docs/missing.md",
        target_suffix="#intro",
        status="unresolved",
        evidence_fingerprint="offset:8:29",
        target_locator_type="KB_PATH",
        target_locator_value="/docs/missing.md",
    )

    sql, params = cursor.executed[0]
    assert "INSERT INTO knowledge_file_reference" in sql
    assert params["relation_code"] == "MENTIONS"
    assert params["discovered_by"] == "MARKDOWN_PARSER"
    assert params["status"] == "unresolved"
    assert params["target_locator_type"] == "KB_PATH"


async def test_upsert_semantic_relation_is_an_entity_surface_adapter():
    cursor = FakeCursor(fetchone_results=[{"kid": 31}])
    repo = KnowledgeFileReferenceRepository()

    await repo.upsert_semantic_relation(
        cursor,
        knowledge_base_id=2,
        source_fs_entry_id=7,
        target_fs_entry_id=8,
        relation_code="IS_A",
        original_target="数据库",
        confidence=0.91,
        discovered_by="ENTITY_ENRICH",
        definition_version="v1",
        source_task_id=99,
    )

    _, params = cursor.executed[0]
    assert params["target_locator_type"] == "ENTITY_SURFACE"
    assert params["target_locator_value"] == "数据库"
    assert params["producer_run_id"] == "99"
    assert params["source_task_id"] == 99


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"relation_code": "UNKNOWN"}, "relation_code must be one of"),
        ({"confidence": 1.1}, "confidence must be between"),
        ({"start_line": 2}, "must be provided together"),
        (
            {"start_offset": 5, "end_offset": 4},
            "invalid source offset range",
        ),
        (
            {"target_locator_type": "URL"},
            "target_locator_type must be",
        ),
    ],
)
async def test_upsert_relation_assertion_validates_contract(overrides, message):
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeCursor()
    values = {
        "knowledge_base_id": 1,
        "source_fs_entry_id": 2,
        "target_fs_entry_id": 3,
        "original_target": "Foo",
        "discovered_by": "ENTITY_DISCOVERY",
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        await repo.upsert_relation_assertion(cursor, **values)

    assert cursor.executed == []


async def test_upsert_semantic_relation_rejects_self_edge():
    repo = KnowledgeFileReferenceRepository()

    with pytest.raises(ValueError, match="source and target must differ"):
        await repo.upsert_semantic_relation(
            FakeCursor(),
            knowledge_base_id=1,
            source_fs_entry_id=2,
            target_fs_entry_id=2,
            relation_code="MENTIONS",
            original_target="Self",
        )


async def test_delete_outgoing_scopes_by_source_relation_and_producer_run():
    rows = [{"kid": 71}]
    cursor = FakeCursor(fetchall_results=[rows])
    repo = KnowledgeFileReferenceRepository()

    result = await repo.delete_outgoing_for_source_fs_entry_id(
        cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        relation_code=["MENTIONS", "DEPENDS_ON"],
        discovered_by="ENTITY_DISCOVERY",
        producer_run_id="task-9",
    )

    assert result == rows
    sql, params = cursor.executed[0]
    assert "source_fs_entry_id = %(source_fs_entry_id)s" in sql
    assert "relation_code = ANY(%(relation_codes)s)" in sql
    assert "discovered_by = ANY(%(discovered_by_values)s)" in sql
    assert "producer_run_id = %(producer_run_id)s" in sql
    assert "reference_type" not in sql
    assert params["relation_codes"] == ["MENTIONS", "DEPENDS_ON"]
    assert params["discovered_by_values"] == ["ENTITY_DISCOVERY"]


async def test_legacy_source_delete_only_owns_markdown_parser_assertions():
    cursor = FakeCursor(fetchall_results=[[]])
    repo = KnowledgeFileReferenceRepository()

    await repo.delete_for_source_fs_entry_id(cursor, source_fs_entry_id=9)

    sql, params = cursor.executed[0]
    assert "reference_type" not in sql
    assert "discovered_by = ANY(%(discovered_by_values)s)" in sql
    assert params["discovered_by_values"] == ["MARKDOWN_PARSER"]


async def test_semantic_delete_adapter_never_deletes_markdown_mentions():
    cursor = FakeCursor(fetchall_results=[[]])
    repo = KnowledgeFileReferenceRepository()

    await repo.delete_semantic_for_source_fs_entry_id(
        cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        relation_code="MENTIONS",
    )

    sql, params = cursor.executed[0]
    assert "reference_type" not in sql
    assert params["relation_codes"] == ["MENTIONS"]
    assert params["discovered_by_values"] == ["ENTITY_DISCOVERY"]


async def test_list_by_reference_ids_resolves_any_assertion_id():
    cursor = FakeCursor(fetchall_results=[[{"kid": 11}, {"kid": 12}]])
    repo = KnowledgeFileReferenceRepository()

    rows = await repo.list_by_reference_ids(cursor, reference_ids=[11, 12])

    assert rows == [{"kid": 11}, {"kid": 12}]
    sql, params = cursor.executed[0]
    assert "kfr.kid = ANY(%(reference_ids)s)" in sql
    assert "reference_type" not in sql
    assert "target_locator_type" in sql
    assert params == {"reference_ids": [11, 12]}


async def test_logical_relation_list_deduplicates_assertions_by_edge():
    cursor = FakeCursor(fetchall_results=[[{"kid": 51}]])
    repo = KnowledgeFileReferenceRepository()

    rows = await repo.list_relations_by_source(
        cursor,
        knowledge_base_id=3,
        source_fs_entry_id=11,
        relation_code=["MENTIONS", "DEPENDS_ON"],
        limit=20,
        offset=40,
    )

    assert rows == [{"kid": 51}]
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert "ROW_NUMBER() OVER" in normalized
    assert "COUNT(*) OVER" in normalized
    assert (
        "PARTITION BY kfr.source_fs_entry_id, kfr.relation_code, kfr.target_fs_entry_id"
    ) in normalized
    assert "WHERE assertion_rank = 1" in normalized
    assert "WHEN kfr.start_line IS NOT NULL" in normalized
    assert "reference_type" not in normalized
    assert params["relation_codes"] == ["MENTIONS", "DEPENDS_ON"]
    assert params["limit"] == 20
    assert params["offset"] == 40


async def test_logical_relation_count_groups_source_relation_and_target():
    cursor = FakeCursor(fetchone_results=[{"total": 4}])
    repo = KnowledgeFileReferenceRepository()

    total = await repo.count_relations_by_target(
        cursor,
        knowledge_base_id=3,
        target_fs_entry_id=12,
        relation_code="IS_A",
    )

    assert total == 4
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split())
    assert (
        "GROUP BY kfr.source_fs_entry_id, kfr.relation_code, kfr.target_fs_entry_id"
    ) in normalized
    assert "reference_type" not in normalized
    assert params["relation_codes"] == ["IS_A"]


async def test_path_resolution_changes_current_target_but_preserves_locator():
    cursor = FakeCursor(fetchall_results=[[{"kid": 21}]])
    repo = KnowledgeFileReferenceRepository()

    await repo.resolve_pending_for_path(
        cursor,
        knowledge_base_id=1,
        target_path="/docs/restored.md",
        target_fs_entry_id=7,
    )

    sql, _ = cursor.executed[0]
    set_clause = sql.split("WHERE", maxsplit=1)[0]
    assert "target_fs_entry_id = %(target_fs_entry_id)s" in set_clause
    assert "SET target_locator_type" not in set_clause
    assert ",\n                target_locator_type =" not in set_clause
    assert ",\n                target_locator_value =" not in set_clause
    assert "target_path = %(target_path)s" in sql
    assert "target_locator_type = 'KB_PATH'" not in sql
    assert "reference_type" not in sql


async def test_entity_surface_locator_can_rebind_to_a_new_target_row():
    cursor = FakeCursor(fetchall_results=[[{"kid": 22}]])
    repo = KnowledgeFileReferenceRepository()

    rows = await repo.resolve_assertions_for_locator(
        cursor,
        knowledge_base_id=1,
        target_locator_type="entity_surface",
        target_locator_value="数据库",
        target_fs_entry_id=17,
    )

    assert rows == [{"kid": 22}]
    sql, params = cursor.executed[0]
    set_clause = sql.split("FROM knowledge_fs_entry", maxsplit=1)[0]
    assert "target_fs_entry_id = target.kid" in set_clause
    assert "SET target_locator_type" not in set_clause
    assert ",\n                target_locator_type =" not in set_clause
    assert ",\n                target_locator_value =" not in set_clause
    assert "target.knowledge_base_id = %(knowledge_base_id)s" in sql
    assert "assertion.target_locator_type = %(target_locator_type)s" in sql
    assert params["target_locator_type"] == "ENTITY_SURFACE"
    assert params["target_locator_value"] == "数据库"


async def test_mark_deleted_refreshes_path_locator_and_preserves_other_locators():
    cursor = FakeCursor(fetchall_results=[[{"kid": 31}]])
    repo = KnowledgeFileReferenceRepository()

    await repo.mark_targets_deleted(
        cursor,
        knowledge_base_id=1,
        targets=[(7, "/docs/a.md")],
    )

    sql, params = cursor.executed[0]
    set_clause = sql.split("FROM (VALUES", maxsplit=1)[0]
    assert "SET target_locator_type" not in set_clause
    assert "WHEN kfr.target_locator_type = 'KB_PATH'" in set_clause
    assert "THEN deleted_targets.target_path" in set_clause
    assert "ELSE kfr.target_locator_value" in set_clause
    assert "target_path = deleted_targets.target_path" in set_clause
    assert params["target_0_path"] == "/docs/a.md"


def test_historical_026_remains_free_of_assertion_extensions():
    historical_sql = " ".join(
        HISTORICAL_SCHEMA_PATH.read_text(encoding="utf-8").split()
    )

    for extension_name in (
        "relation_code",
        "discovered_by",
        "producer_run_id",
        "evidence_fingerprint",
        "target_locator_type",
        "source_task_id",
    ):
        assert extension_name not in historical_sql


def test_030_defines_unified_assertion_contract_and_heals_draft_schema():
    sql = " ".join(ASSERTION_MIGRATION_PATH.read_text(encoding="utf-8").split())

    assert "relation_code varchar(32) NOT NULL DEFAULT 'MENTIONS'" in sql
    assert "discovered_by varchar(32) NOT NULL DEFAULT 'MARKDOWN_PARSER'" in sql
    for column in (
        "producer_run_id",
        "evidence_fingerprint",
        "source_heading_path",
        "start_line",
        "end_line",
        "start_offset",
        "end_offset",
        "target_locator_type",
        "target_locator_value",
        "confidence",
        "definition_version",
        "source_task_id",
    ):
        assert f"column_name = '{column}'" in sql
    assert "REFERENCES knowledge_semantic_processing_task(kid)" in sql
    assert "ALTER COLUMN evidence_fingerprint SET NOT NULL" in sql
    assert "ALTER COLUMN target_locator_type SET NOT NULL" in sql
    assert "ALTER COLUMN target_locator_value SET NOT NULL" in sql
    assert "ALTER COLUMN relation_code SET DEFAULT 'MENTIONS'" in sql
    assert "ALTER COLUMN relation_code SET NOT NULL" in sql
    assert "ALTER COLUMN discovered_by SET DEFAULT 'MARKDOWN_PARSER'" in sql
    assert "ALTER COLUMN discovered_by SET NOT NULL" in sql
    assert "'KB_PATH', 'ENTITY_SURFACE', 'FS_ENTRY_ID'" in sql
    assert "chk_kfr_relation_code" in sql
    assert "chk_kfr_confidence" in sql
    assert "chk_kfr_source_lines" in sql
    assert "chk_kfr_source_offsets" in sql
    assert "chk_kfr_target_locator" in sql
    assert "ALTER COLUMN target_suffix DROP NOT NULL" in sql
    assert "uq_kfr_exact_assertion" in sql
    assert "COALESCE(producer_run_id, '__NO_RUN__')" in sql
    assert "indexdef NOT LIKE '%__NO_RUN__%'" in sql
    assert "evidence_fingerprint" in sql
    assert "idx_kfr_relation_source" in sql
    assert "idx_kfr_relation_target" in sql
    assert "idx_kfr_producer_outgoing" in sql
    assert "idx_kfr_source_task" in sql
    assert "column_name = 'reference_type'" in sql
    assert "WHEN reference_type = ''SEMANTIC''" in sql
    assert "target_locator_type = ''ENTITY_SURFACE''" in sql
    assert "assertion.reference_type = ''MARKDOWN''" in sql
    for legacy_constraint in (
        "chk_knowledge_file_reference_type",
        "chk_knowledge_file_reference_semantic_state",
        "chk_knowledge_file_reference_confidence",
    ):
        assert f"DROP CONSTRAINT {legacy_constraint}" in sql
    for legacy_index in (
        "uq_kfr_semantic_relation",
        "idx_kfr_semantic_source",
        "idx_kfr_semantic_target",
        "idx_kfr_semantic_source_task",
    ):
        assert f"DROP INDEX IF EXISTS {legacy_index}" in sql
    assert "DROP COLUMN reference_type" in sql
