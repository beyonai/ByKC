"""Evaluate KnowledgeEntity discovery stability on repository documents.

The second pass simulates the state after the first document has populated the
entity vocabulary.  A healthy content-driven extractor should keep selecting the
same semantic identities and merely resolve them to existing entity IDs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    AhoCorasickIndex,
    EntityCandidate,
    KnowledgeEntityDiscovery,
    OpenAICompatibleKnowledgeEntityLLM,
    SurfaceEntry,
    SurfaceMatch,
    SurfacePosting,
    normalize_surface,
)


@dataclass(frozen=True, slots=True)
class PassResult:
    names: list[str]
    evidence_validity: float
    required_recall: float
    forbidden_names: list[str]


@dataclass(frozen=True, slots=True)
class DocumentResult:
    document: str
    baseline: PassResult
    repeated: PassResult
    populated_vocabulary: PassResult
    repeat_jaccard: float
    vocabulary_jaccard: float
    passed: bool


def _identity_names(candidates: tuple[EntityCandidate, ...]) -> set[str]:
    return {normalize_surface(candidate.entity_name) for candidate in candidates}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _pass_result(
    markdown: str,
    candidates: tuple[EntityCandidate, ...],
    *,
    required_any_of: list[list[str]],
    forbidden: list[str],
) -> PassResult:
    normalized_document = normalize_surface(markdown)
    normalized_names = [
        normalize_surface(candidate.entity_name) for candidate in candidates
    ]
    evidence_hits = sum(
        normalize_surface(candidate.evidence) in normalized_document
        for candidate in candidates
    )
    validity = evidence_hits / len(candidates) if candidates else 1.0
    required_hits = sum(
        any(
            normalize_surface(alternative) in entity_name
            for alternative in alternatives
            for entity_name in normalized_names
        )
        for alternatives in required_any_of
    )
    required_recall = required_hits / len(required_any_of) if required_any_of else 1.0
    forbidden_names = [
        candidate.entity_name
        for candidate in candidates
        if any(
            normalize_surface(term) in normalize_surface(candidate.entity_name)
            for term in forbidden
        )
    ]
    return PassResult(
        names=[candidate.entity_name for candidate in candidates],
        evidence_validity=round(validity, 4),
        required_recall=round(required_recall, 4),
        forbidden_names=forbidden_names,
    )


def _known_matches(
    markdown: str, candidates: tuple[EntityCandidate, ...]
) -> tuple[SurfaceMatch, ...]:
    candidate_ids = {
        normalize_surface(candidate.entity_name): 10_000 + index
        for index, candidate in enumerate(candidates)
    }
    entries: list[SurfaceEntry] = []
    for candidate in candidates:
        subject_id = (
            candidate_ids.get(normalize_surface(candidate.subject_entity_name or ""))
            if candidate.subject_entity_name
            else None
        )
        posting = SurfacePosting(
            entity_file_id=candidate_ids[normalize_surface(candidate.entity_name)],
            knowledge_base_id=1,
            entity_name=candidate.entity_name,
            subject_file_id=subject_id,
        )
        surfaces = {
            candidate.entity_name,
            candidate.local_name,
            *candidate.aliases,
        }
        for surface in surfaces:
            if surface.strip():
                entries.append(SurfaceEntry(surface=surface, posting=posting))

    index = AhoCorasickIndex(entries)
    initial = index.scan(markdown, current_knowledge_base_id=1)
    subject_context = {
        posting.entity_file_id
        for match in initial
        for posting in match.anchorable_postings
        if posting.subject_file_id is None
    }
    return index.scan(
        markdown,
        current_knowledge_base_id=1,
        subject_context_file_ids=subject_context,
    )


async def _evaluate_document(
    path: Path,
    discovery: KnowledgeEntityDiscovery,
    *,
    expectations: dict[str, object],
    minimum_jaccard: float,
    minimum_evidence_validity: float,
    minimum_required_recall: float,
) -> DocumentResult:
    markdown = path.read_text(encoding="utf-8")
    baseline = await discovery.discover(markdown)
    repeated = await discovery.discover(markdown)
    populated = await discovery.discover(
        markdown,
        known_matches=_known_matches(markdown, baseline.candidates),
    )
    baseline_names = _identity_names(baseline.candidates)
    repeated_names = _identity_names(repeated.candidates)
    populated_names = _identity_names(populated.candidates)
    repeat_jaccard = _jaccard(baseline_names, repeated_names)
    vocabulary_jaccard = _jaccard(baseline_names, populated_names)
    required_any_of = [
        [str(alternative) for alternative in alternatives]
        for alternatives in expectations.get("requiredAnyOf", [])
    ]
    forbidden = [str(value) for value in expectations.get("forbidden", [])]
    pass_results = tuple(
        _pass_result(
            markdown,
            candidates,
            required_any_of=required_any_of,
            forbidden=forbidden,
        )
        for candidates in (
            baseline.candidates,
            repeated.candidates,
            populated.candidates,
        )
    )
    passed = (
        repeat_jaccard >= minimum_jaccard
        and vocabulary_jaccard >= minimum_jaccard
        and all(
            result.evidence_validity >= minimum_evidence_validity
            and result.required_recall >= minimum_required_recall
            and not result.forbidden_names
            for result in pass_results
        )
    )
    return DocumentResult(
        document=path.as_posix(),
        baseline=pass_results[0],
        repeated=pass_results[1],
        populated_vocabulary=pass_results[2],
        repeat_jaccard=round(repeat_jaccard, 4),
        vocabulary_jaccard=round(vocabulary_jaccard, 4),
        passed=passed,
    )


async def _main(args: argparse.Namespace) -> int:
    llm = OpenAICompatibleKnowledgeEntityLLM(temperature=0.0)
    discovery = KnowledgeEntityDiscovery(llm)
    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    results = await asyncio.gather(
        *(
            _evaluate_document(
                path,
                discovery,
                expectations=benchmark.get(path.name, {}),
                minimum_jaccard=args.minimum_jaccard,
                minimum_evidence_validity=args.minimum_evidence_validity,
                minimum_required_recall=args.minimum_required_recall,
            )
            for path in args.documents
        )
    )
    report = {
        "thresholds": {
            "minimumJaccard": args.minimum_jaccard,
            "minimumEvidenceValidity": args.minimum_evidence_validity,
            "minimumRequiredRecall": args.minimum_required_recall,
        },
        "passed": all(result.passed for result in results),
        "documents": [asdict(result) for result in results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "documents",
        nargs="*",
        type=Path,
        default=sorted(Path("Document/docs").glob("*.md")),
    )
    parser.add_argument("--minimum-jaccard", type=float, default=0.85)
    parser.add_argument("--minimum-evidence-validity", type=float, default=1.0)
    parser.add_argument("--minimum-required-recall", type=float, default=0.75)
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("scripts/knowledge_base/entity_discovery_benchmark.json"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
