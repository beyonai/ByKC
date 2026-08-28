from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    DiscoveredEntity,
    DiscoveredTopic,
    DiscoveryResult,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import EvidenceFragment

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "knowledge_base"
    / "evaluate_entity_enrich_quality.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "evaluate_entity_enrich_quality", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
analyze_context = _MODULE.analyze_context
analyze_document = _MODULE.analyze_document
split_evidence_for_incremental_replay = _MODULE.split_evidence_for_incremental_replay
run_bounded_concurrently = _MODULE.run_bounded_concurrently


def fragment(file_id: int, path: str, content: str) -> EvidenceFragment:
    return EvidenceFragment(file_id, path, content, authorized=True)


def test_cli_defaults_to_four_concurrent_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT_PATH)])

    args = _MODULE._args()

    assert args.concurrency == 4


def test_two_stage_candidate_matches_authoritative_alias_only() -> None:
    discovery = DiscoveryResult(
        entities=(
            DiscoveredEntity("E1", "Hermes", (), "other"),
            DiscoveredEntity("E2", "Moltbot", ("clawdbot",), "target"),
        ),
        topics=(),
    )

    candidate = _MODULE.match_discovered_entity(
        discovery,
        entity_name="OpenClaw",
        aliases=("Moltbot", "clawdbot"),
    )

    assert candidate is not None
    assert candidate.entity_ref == "E2"


def test_two_stage_topics_are_owner_scoped_and_incremental() -> None:
    discovery = DiscoveryResult(
        entities=(),
        topics=(
            DiscoveredTopic("E1", "上下文工程", "first"),
            DiscoveredTopic("E2", "上下文工程", "wrong owner"),
            DiscoveredTopic("E1", "双源记忆系统", "new"),
        ),
    )

    current = _MODULE.topics_owned_by_candidate(discovery, entity_ref="E1")
    delta = _MODULE.incremental_topic_delta(("上下文工程",), current)

    assert current == ("上下文工程", "双源记忆系统")
    assert delta == ("双源记忆系统",)


def test_two_stage_citation_transition_requires_old_and_current_source() -> None:
    transition = _MODULE.citation_transition(
        baseline_markdown="# OpenClaw\n\n[旧来源](/first/article.md)",
        updated_markdown=(
            "# OpenClaw\n\n[旧来源](/first/article.md) 与 [second](/second/article.md)"
        ),
        stage_source="/second/article.md",
    )

    assert transition["oldReferencesPreserved"] is True
    assert transition["stageSourceReferenced"] is True


@pytest.mark.asyncio
async def test_bounded_runner_preserves_order_and_limits_concurrency() -> None:
    active = 0
    max_active = 0

    async def evaluate(row: dict) -> dict:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01 * (4 - row["id"]))
        active -= 1
        return {"id": row["id"]}

    results = await run_bounded_concurrently(
        [{"id": 1}, {"id": 2}, {"id": 3}],
        concurrency=2,
        evaluate=evaluate,
    )

    assert results == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert max_active == 2


def test_document_gate_rejects_definition_stub_for_substantive_evidence() -> None:
    result = analyze_document(
        entity_name="Entity",
        markdown="# Entity\n\n## 实体定义与边界\n\n一句定义。[source](/source.md)",
        existing_markdown="# Entity",
        evidence=[fragment(2, "/source.md", "evidence " * 200)],
    )

    assert result["checks"]["notDefinitionStub"] is False
    assert result["passed"] is False


def test_document_gate_preserves_old_links_and_rejects_unknown_links() -> None:
    result = analyze_document(
        entity_name="Entity",
        markdown=(
            "# Entity\n\n## 定义\n\n正文 [old](/old.md)。\n\n"
            "## 机制\n\n机制 [new](/new.md) [unknown](/unknown.md)。"
        ),
        existing_markdown="# Entity\n\n旧事实 [old](/old.md)。",
        evidence=[fragment(2, "/new.md", "new evidence")],
    )

    assert result["checks"]["oldReferencesPreserved"] is True
    assert result["checks"]["onlyAuthorizedReferences"] is False
    assert result["unauthorizedReferences"] == ["/unknown.md"]


def test_document_gate_treats_encoded_and_decoded_paths_as_same_reference() -> None:
    result = analyze_document(
        entity_name="Entity",
        markdown=(
            "# Entity\n\n## 定义\n\n正文 "
            "[source](/%E5%A4%A7%E5%8E%82%E6%96%87%E7%AB%A0/%E6%9D%A5%E6%BA%90.md)。"
        ),
        existing_markdown="# Entity\n\n旧事实 [source](/大厂文章/来源.md)。",
        evidence=[],
    )

    assert result["checks"]["oldReferencesPreserved"] is True
    assert result["checks"]["onlyAuthorizedReferences"] is True


def test_document_gate_rejects_mechanical_citation_distribution() -> None:
    paragraphs = "\n\n".join(
        f"第 {index} 段 [source](/source.md)。" for index in range(1, 7)
    )
    result = analyze_document(
        entity_name="Entity",
        markdown=f"# Entity\n\n## 定义\n\n{paragraphs}\n\n## 参考资料\n\n- [source](/source.md)",
        existing_markdown="# Entity",
        evidence=[fragment(2, "/source.md", "evidence " * 200)],
    )

    assert result["checks"]["citationsNotMechanical"] is False
    assert result["trailingCitationRatio"] > 0.8


def test_document_gate_allows_external_link_explicitly_present_in_evidence() -> None:
    result = analyze_document(
        entity_name="Entity",
        markdown=(
            "# Entity\n\n## 定义\n\n"
            "正文 [official](https://example.com/project) [source](/source.md)。"
        ),
        existing_markdown="# Entity",
        evidence=[
            fragment(
                2,
                "/source.md",
                "Official project: https://example.com/project",
            )
        ],
    )

    assert result["checks"]["onlyAuthorizedReferences"] is True


def test_incremental_replay_splits_whole_sources_before_fragments() -> None:
    evidence = [
        fragment(3, "/three.md", "a"),
        fragment(2, "/two.md", "b"),
        fragment(3, "/three.md", "c"),
        fragment(4, "/four.md", "d"),
    ]

    baseline, delta = split_evidence_for_incremental_replay(evidence)

    assert {item.document_file_id for item in baseline} == {2}
    assert {item.document_file_id for item in delta} == {3, 4}


def test_context_analysis_reports_topic_batches_and_budget() -> None:
    evidence = [fragment(2, "/source.md", "x" * 500)]
    result = analyze_context(
        topics=tuple(f"T{index}" for index in range(13)),
        evidence=evidence,
        messages=[{"role": "user", "content": "prompt"}],
    )

    assert result["queryBatchCount"] == 3
    assert result["withinEvidenceBudget"] is True
    assert result["allAuthorized"] is True
