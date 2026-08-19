import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_demo():
    path = Path("scripts/knowledge_base/evaluate_entity_synonym_resolution.py")
    spec = spec_from_file_location("entity_synonym_resolution_demo", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


demo = _load_demo()


def _entities():
    return [
        demo.EntityRecord(
            name="ByKC-BaseQAEngine",
            subject="ByKC",
            local_name="BaseQAEngine",
            aliases=(),
            source_documents=("Document/ByKC.md",),
            origins=("llm_discovered",),
        ),
        demo.EntityRecord(
            name="ByKC-FastQAEngine",
            subject="ByKC",
            local_name="FastQAEngine",
            aliases=(),
            source_documents=("Document/ByKC.md",),
            origins=("llm_discovered",),
        ),
        demo.EntityRecord(
            name="GBrain-Hybrid Search",
            subject="GBrain",
            local_name="Hybrid Search",
            aliases=("混合检索",),
            source_documents=("Document/GBrain.md",),
            origins=("llm_discovered",),
        ),
    ]


def test_normalization_splits_camel_case_and_separators():
    assert demo.normalize_text("ByKC-BaseQAEngine") == "by kc base qa engine"
    assert demo.normalize_text("BYKC_Base_QA_Engine") == "bykc base qa engine"


def test_extract_local_mention_removes_subject_and_separator():
    assert demo.extract_local_mention("ByKC-基础问答引擎", "ByKC") == "基础问答引擎"
    assert (
        demo.extract_local_mention("Palantir：对象时间线", "Palantir") == "对象时间线"
    )
    assert (
        demo.extract_local_mention("GBrain_Hybrid Search", "gbrain") == "Hybrid Search"
    )


def test_extract_local_mention_keeps_unrelated_or_subject_only_mentions():
    assert demo.extract_local_mention("对象时间线", "Palantir") == "对象时间线"
    assert demo.extract_local_mention("Palantir", "Palantir") == "Palantir"


def test_lexical_candidates_keep_same_subject_cross_language_case():
    entities = [
        *_entities(),
        demo.EntityRecord(
            name="ByKC",
            subject="",
            local_name="ByKC",
            aliases=(),
            source_documents=("Document/ByKC.md",),
            origins=("llm_discovered",),
        ),
    ]
    candidates = demo.lexical_candidates(
        "ByKC-基础问答引擎",
        entities,
        demo.LexicalScorer(entities),
        subject="ByKC",
    )
    assert {candidate.entity_name for candidate in candidates} >= {"ByKC-BaseQAEngine"}
    assert all(
        not candidate.entity_name.startswith("GBrain-") for candidate in candidates
    )
    assert all(candidate.entity_name != "ByKC" for candidate in candidates)


def test_alias_is_ranked_first_by_lexical_retrieval():
    entities = _entities()
    candidates = demo.lexical_candidates(
        "GBrain-混合检索",
        entities,
        demo.LexicalScorer(entities),
        subject="GBrain",
    )
    assert candidates[0].entity_name == "GBrain-Hybrid Search"


def test_subject_root_is_retained_when_longer_mention_is_its_exact_alias():
    root = demo.EntityRecord(
        name="AI Argos",
        subject="",
        local_name="AI Argos",
        aliases=("AI Argos 组织操作系统",),
        source_documents=("Document/organization.md",),
        origins=("llm_discovered",),
    )
    child = demo.EntityRecord(
        name="AI Argos-HACU",
        subject="AI Argos",
        local_name="HACU",
        aliases=(),
        source_documents=("Document/organization.md",),
        origins=("llm_discovered",),
    )
    entities = [root, child]
    candidates = demo.lexical_candidates(
        "AI Argos 组织操作系统",
        entities,
        demo.LexicalScorer(entities),
    )
    assert candidates[0].entity_name == "AI Argos"


def test_hybrid_uses_reciprocal_rank_fusion():
    lexical = (
        demo.RankedCandidate("A", 0.9, 0.9, None, 0.3),
        demo.RankedCandidate("B", 0.8, 0.8, None, 0.3),
    )
    embedding = (
        demo.RankedCandidate("B", 0.95, 0.8, 0.95, 0.3),
        demo.RankedCandidate("C", 0.9, 0.1, 0.9, 0.3),
    )
    fused = demo.hybrid_candidates(lexical, embedding)
    assert fused[0].entity_name == "B"


def test_embedding_candidates_use_best_full_or_local_similarity():
    entities = [
        demo.EntityRecord(
            name="Palantir-OntologyReasoner",
            subject="Palantir",
            local_name="OntologyReasoner",
            aliases=(),
            source_documents=("synthetic",),
            origins=("synthetic_stress",),
        ),
        demo.EntityRecord(
            name="object timeline",
            subject="",
            local_name="object timeline",
            aliases=(),
            source_documents=("Document/Palantir.md",),
            origins=("llm_discovered",),
        ),
    ]
    candidates = demo.embedding_candidates(
        mention_vector=(1.0, 0.0),
        mention_local_vector=(0.0, 1.0),
        entity_vectors=((0.8, 0.6), (0.6, 0.8)),
        entity_local_vectors=((1.0, 0.0), (0.0, 1.0)),
        entities=entities,
        scorer=demo.LexicalScorer(entities),
        mention="Palantir-对象时间线",
        subject="Palantir",
        top_k=2,
    )
    assert candidates[0].entity_name == "object timeline"
    assert candidates[0].embedding_score == 1.0


def test_metrics_report_false_merges_separately():
    rows = [
        {
            "expected": "SAME",
            "targetStatus": "resolved",
            "lexical": {"expected_rank": 1, "resolution_correct": True},
        },
        {
            "expected": "NEW",
            "targetStatus": "not_applicable",
            "lexical": {"expected_rank": None, "resolution_correct": False},
        },
    ]
    metrics = demo.aggregate_metrics(rows, "lexical")
    assert metrics["recallAt1"] == 1.0
    assert metrics["positiveResolutionAccuracy"] == 1.0
    assert metrics["falseMergeRate"] == 1.0
    assert metrics["resolutionAccuracy"] == 0.5


def test_manual_benchmark_contains_positive_and_negative_cases():
    raw = __import__("json").loads(
        Path(
            "scripts/knowledge_base/entity_synonym_resolution_benchmark.json"
        ).read_text(encoding="utf-8")
    )
    outcomes = {case["expected"] for case in raw["cases"]}
    assert outcomes == {"SAME", "NEW"}


def test_manual_fixture_is_auditable_and_does_not_replace_llm_origin(tmp_path):
    fixture = tmp_path / "fixtures.json"
    fixture.write_text(
        '{"entities":[{"entityName":"ByKC-BaseQAEngine","aliases":["基础问答引擎"]}]}',
        encoding="utf-8",
    )
    catalog = demo.apply_catalog_fixtures(_entities(), fixture)
    entity = next(item for item in catalog if item.name == "ByKC-BaseQAEngine")
    assert entity.aliases == ("基础问答引擎",)
    assert entity.origins == ("llm_discovered", "manual_fixture")


def test_extraction_cache_validates_each_document_hash(tmp_path):
    document = tmp_path / "document.md"
    document.write_text("version one", encoding="utf-8")
    cache = tmp_path / "extracted.json"
    fingerprint = __import__("hashlib").sha256(document.read_bytes()).hexdigest()
    cache.write_text(
        __import__("json").dumps(
            {
                "cacheVersion": demo.EXTRACTION_CACHE_VERSION,
                "documents": {document.as_posix(): {"sha256": fingerprint}},
            }
        ),
        encoding="utf-8",
    )
    assert demo.valid_extraction_cache_count([document], cache) == 1
    document.write_text("version two", encoding="utf-8")
    assert demo.valid_extraction_cache_count([document], cache) == 0


async def test_adjudication_sends_candidate_aliases_to_llm():
    class RecordingLLM:
        messages = None

        async def complete(self, messages, *, json_mode=False):
            self.messages = messages
            assert json_mode is True
            return '{"ByKC-OPERATION_REGISTRY":"SAME"}'

    llm = RecordingLLM()
    case = demo.BenchmarkCase(
        case_id="registry",
        mention="ByKC-操作类型注册表",
        expected="SAME",
        target_any_of=("ByKC-OPERATION_REGISTRY",),
        subject="ByKC",
        context="操作类型注册表负责映射操作规范。",
        category="alias",
        source="test",
    )
    entity = demo.EntityRecord(
        name="ByKC-OPERATION_REGISTRY",
        subject="ByKC",
        local_name="OPERATION_REGISTRY",
        aliases=("操作类型注册表",),
        source_documents=("Document/ByKC.md",),
        origins=("llm_discovered",),
    )
    decisions = await demo.adjudicate(llm, case, [entity])
    assert decisions == {"ByKC-OPERATION_REGISTRY": "SAME"}
    assert "操作类型注册表" in llm.messages[1]["content"]


def test_stress_vocabulary_expands_catalog_without_overwriting_benchmark_mentions():
    case = demo.BenchmarkCase(
        case_id="negative",
        mention="ByKC-BaseRetrievalEngine",
        expected="NEW",
        target_any_of=(),
        subject="ByKC",
        context="",
        category="hard_negative",
        source="test",
    )
    expanded = demo.expand_catalog_for_stress_test(
        _entities(),
        [case],
        Path("scripts/knowledge_base/entity_synonym_resolution_stress_vocabulary.json"),
        addition_count=100,
    )
    assert len(expanded) == len(_entities()) + 100
    assert all(entity.name != case.mention for entity in expanded)
    assert all(entity.name != "ByKC-BaseRetrievalEngine" for entity in expanded)
    stress_entities = [
        entity for entity in expanded if "synthetic_stress" in entity.origins
    ]
    assert len(stress_entities) == 100
    assert any(entity.subject == "ByKC" for entity in stress_entities)
    assert any(not entity.subject for entity in stress_entities)
