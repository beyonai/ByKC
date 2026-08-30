from __future__ import annotations

import json
from pathlib import Path

import pytest

from by_qa.knowledge_base.repositories.knowledge_entity_asset_repository import (
    KnowledgeEntityAssetRepository,
)
from by_qa.knowledge_base.services.knowledge_entity_synonym_resolution import (
    KnowledgeEntitySynonymAdjudicator,
    SynonymDecision,
)


class _Cursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params = {}

    async def execute(self, sql, params) -> None:
        self.sql = sql
        self.params = params

    async def fetchone(self):
        return {"kid": 7}


class _LLM:
    def __init__(self) -> None:
        self.messages = []

    async def complete(self, messages, *, json_mode=False):
        assert json_mode is True
        self.messages = messages
        return json.dumps(
            {
                "decision": "DIFFERENT",
                "selectedCandidateId": None,
                "canonicalName": None,
                "aliasToAdd": None,
                "reasonCode": "RELATED_PRODUCT",
            }
        )


def test_consolidated_schema_adds_terminal_description_shape() -> None:
    migration = Path(
        "src/by_qa/knowledge_base/sql/035_knowledge_entity_discovery_schema.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN description text NULL" in migration
    assert "evidence_summary" not in migration
    assert "chk_knowledge_entity_description_shape" in migration
    assert "object_kind = 'ENTITY'" in migration
    assert "name_role = 'canonical'" in migration


@pytest.mark.asyncio
async def test_create_canonical_persists_description() -> None:
    cursor = _Cursor()
    repository = KnowledgeEntityAssetRepository("knowledge_entity_embedding_test")

    await repository.create_canonical(
        cursor,
        knowledge_base_id=1,
        entity_name="OpenClaw",
        normalized_entity_name="openclaw",
        subject_entity_id=None,
        entity_type=None,
        description="OpenClaw 是开源个人 AI 助手。",
    )

    assert "description" in cursor.sql
    assert cursor.params["description"] == "OpenClaw 是开源个人 AI 助手。"
    assert "evidence_summary" not in cursor.params


@pytest.mark.asyncio
async def test_adjudicator_receives_descriptions_and_current_evidence() -> None:
    llm = _LLM()
    adjudicator = KnowledgeEntitySynonymAdjudicator(llm)

    result = await adjudicator.adjudicate(
        mention="ClawHub",
        mention_description="ClawHub 是 OpenClaw 的技能市场。",
        evidence="本文直接介绍 ClawHub 的市场定位。",
        subject_name=None,
        entity_type=None,
        candidates=[
            {
                "resolved_entity_id": 7,
                "canonical_entity_name": "OpenClaw",
                "aliases": ["Moltbot"],
                "entity_type": None,
                "description": "OpenClaw 是开源个人 AI 助手。",
                "score": 0.91,
            }
        ],
    )

    payload = json.loads(llm.messages[-1]["content"])
    assert result.decision is SynonymDecision.DIFFERENT
    assert payload["mentionDescription"] == "ClawHub 是 OpenClaw 的技能市场。"
    assert payload["evidence"] == "本文直接介绍 ClawHub 的市场定位。"
    assert payload["candidates"][0]["description"] == ("OpenClaw 是开源个人 AI 助手。")


def test_consolidated_schema_removes_redundant_local_name_storage() -> None:
    entity_migration = Path(
        "src/by_qa/knowledge_base/sql/035_knowledge_entity_discovery_schema.sql"
    ).read_text(encoding="utf-8")
    embedding_migration = Path(
        "src/by_qa/knowledge_base/sql/036_knowledge_entity_embedding_full_only.sql.tpl"
    ).read_text(encoding="utf-8")

    assert "DROP COLUMN local_name" in entity_migration
    assert "DROP COLUMN normalized_local_name" in entity_migration
    assert "normalized_entity_name" in entity_migration
    assert "WHERE representation = 'local_name'" in embedding_migration
    assert "CHECK (representation = 'full')" in embedding_migration


def test_consolidated_schema_has_no_topic_evidence_or_iterative_followups() -> None:
    sql_directory = Path("src/by_qa/knowledge_base/sql")
    deprecated_incremental_migrations = {
        "037_knowledge_entity_evidence_summary.sql",
        "038_knowledge_entity_description.sql",
        "039_knowledge_entity_drop_local_names.sql",
        "040_knowledge_entity_full_embedding_only.sql.tpl",
        "041_drop_knowledge_entity_topic_evidence.sql",
    }
    terminal_schema = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(sql_directory.glob("*.sql*"))
    )

    assert "knowledge_entity_topic_evidence" not in terminal_schema
    assert deprecated_incremental_migrations.isdisjoint(
        path.name for path in sql_directory.iterdir()
    )
