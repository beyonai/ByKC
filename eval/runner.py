"""Eval pipeline: inference (run queries -> JSONL) and judge (JSONL -> report)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.models import CoreInput, StreamEventType
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.services.llm_service import LLMService
from eval.judge import judge
from eval.models import EvalReport, InferenceResult, JudgeVerdict, QueryResult

if TYPE_CHECKING:
    from eval.datasets.base import DatasetSpec


def _build_knowledge_bases(
    spec: DatasetSpec, base_url: str | None = None
) -> list[dict]:
    kb_code = spec.get_kb_code()
    kb_config = spec.kb_config
    effective_base_url = base_url or kb_config.kb_base_url
    kb = {
        "kb_code": kb_code,
        "kb_name": spec.get_kb_name(),
        "kb_description": "",
        "service_name": kb_config.kb_service_name,
        "operations": {
            OperationType.KNOWLEDGE_SEARCH: kb_config.kb_search_url,
        },
    }
    if effective_base_url:
        kb["base_url"] = effective_base_url
    return [kb]


def _build_language_middleware(language: str | None):
    """Build AgentOverride with LanguageMiddleware for each agent."""
    if not language:
        return {}

    from typing import Any

    from langchain.agents.middleware import AgentMiddleware, Runtime
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, SystemMessage
    from langgraph.typing import ContextT, StateT

    class LanguageMiddleware(AgentMiddleware):
        def __init__(self, lang: str) -> None:
            self.lang = lang.strip()

        def _build_system_message(
            self, request: ModelRequest[Any]
        ) -> SystemMessage | None:
            if not self.lang:
                return request.system_message
            instruction = (
                "\n\n## Output Language\n"
                f"Always respond in {self.lang}. "
                "Do not switch to another language unless the user explicitly asks you to."
            )
            existing = request.system_prompt or ""
            return SystemMessage(content=f"{existing}{instruction}")

        async def abefore_model(
            self, state: StateT, runtime: Runtime[ContextT]
        ) -> None:
            pass

        def wrap_model_call(
            self, request: ModelRequest[Any], handler
        ) -> ModelResponse[Any] | AIMessage:
            return handler(
                request.override(system_message=self._build_system_message(request))
            )

        async def awrap_model_call(
            self, request: ModelRequest[Any], handler
        ) -> ModelResponse[Any] | AIMessage:
            return await handler(
                request.override(system_message=self._build_system_message(request))
            )

    middleware = [LanguageMiddleware(language)]

    from by_qa.qa.engines.fast.types import AgentNames as FastAgentNames
    from by_qa.qa.engines.instant.types import AgentNames as InstantAgentNames

    overrides = {}
    for agent_type in (*FastAgentNames, *InstantAgentNames):
        overrides[agent_type] = AgentOverride(middleware=middleware)
    return overrides


async def _run_single_query(
    engine_class,
    config: dict,
    query,
    show_tokens: bool,
) -> QueryResult:
    """Run a single query through the engine and collect results.

    Answer extraction handles two engine paths:
    - Simple question: FINAL_ANSWER node emits an ANSWER event
    - Complex question: SUBANSWER_AGGREGATOR streams tokens via its model;
      we accumulate tokens belonging to that node's instance subtree.
    """
    request = CoreInput(query=query.question, session_id=str(uuid.uuid4()))
    start = time.perf_counter()
    tokens = 0
    answer = ""
    error_msg: str | None = None

    # Instance ids of answer-producing nodes — tokens from their subtree
    # are accumulated as the candidate answer.
    answer_producer_ids: set[str] = set()
    answer_producer_roles = {"final_answer", "subanswer_aggregator"}
    token_buf = ""

    try:
        async with engine_class(config=config) as engine:
            async for event in engine.stream_search(request):
                if event.type == StreamEventType.ERROR:
                    error_msg = event.data.get("error", str(event))

                if event.type == StreamEventType.NODE_START:
                    if event.role in answer_producer_roles and event.instance_id:
                        answer_producer_ids.add(event.instance_id)
                        token_buf = ""

                if event.type == StreamEventType.NODE_END:
                    if event.role in answer_producer_roles:
                        if token_buf and not answer:
                            answer = token_buf
                        if event.instance_id:
                            answer_producer_ids.discard(event.instance_id)

                if event.type == StreamEventType.TOKEN:
                    content = event.data.get("content", "")
                    tokens += 1
                    if show_tokens and content:
                        print(content, end="", flush=True)
                    if content and event.parent_ids:
                        for pid in event.parent_ids:
                            if pid in answer_producer_ids:
                                token_buf += content
                                break

                if event.type == StreamEventType.ANSWER:
                    answer = event.data.get("content", "")
    except Exception as exc:
        error_msg = str(exc)

    if show_tokens and tokens > 0:
        print()

    latency_ms = (time.perf_counter() - start) * 1000

    return QueryResult(
        query=query,
        answer=answer,
        tokens_used=tokens,
        latency_ms=latency_ms,
        error=error_msg,
    )


async def run_inference(
    spec: DatasetSpec,
    mode: str,
    language: str | None = None,
    show_tokens: bool = False,
    sample: int | None = None,
    query_ids: list[str] | None = None,
    retry_failed: bool = False,
    results_path: Path | None = None,
    concurrency: int = 1,
    base_url: str | None = None,
) -> Path:
    """Run queries through the QA engine and save results as JSONL.

    Supports checkpoint/resume: skips queries already present in the output file.
    Use --retry-failed to re-run queries with errors or empty answers.

    Args:
        spec: Dataset spec.
        mode: "instant" or "fast".
        language: Optional output language.
        show_tokens: Print token stream (serialized when concurrency > 1).
        sample: Run only N queries.
        query_ids: Run only specific query IDs.
        retry_failed: Re-run queries with errors/empty answers from JSONL.
        results_path: Path to the JSONL output file (default: datasets/{name}/inference_results.jsonl).
        concurrency: Max concurrent queries.
        base_url: Knowledge base service URL override.

    Returns:
        Path to the results JSONL file.
    """
    if results_path is None:
        results_path = spec.data_dir / "inference_results.jsonl"

    # Load queries
    all_queries = spec.load_queries()
    all_by_id = {q.query_id: q for q in all_queries}

    # Load existing results for checkpoint/resume
    existing: dict[str, InferenceResult] = {}
    if results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = InferenceResult.from_dict(json.loads(line))
                existing[r.query_id] = r
        print(f"Found {len(existing)} existing results in {results_path}")

    if retry_failed:
        # Re-run queries that had errors or empty answers
        failed_ids = [qid for qid, r in existing.items() if r.error or not r.answer]
        if not failed_ids:
            print("No failed queries to retry")
            return results_path
        queries = [all_by_id[qid] for qid in failed_ids if qid in all_by_id]
        print(f"Retrying {len(queries)} failed queries")
    elif query_ids:
        # Run specific query IDs
        id_set = set(query_ids)
        queries = [all_by_id[qid] for qid in id_set if qid in all_by_id]
        if not queries:
            raise ValueError("No queries matched the given --query-ids filter")
    elif sample is not None:
        queries = spec.load_queries_sample(sample)
    else:
        # Full run: skip already completed (successful, non-empty answers)
        pending = [
            q
            for q in all_queries
            if q.query_id not in existing
            or existing[q.query_id].error
            or not existing[q.query_id].answer
        ]
        if not pending:
            print(f"All {len(all_queries)} queries already complete in {results_path}")
            return results_path
        skipped = len(all_queries) - len(pending)
        if skipped:
            print(f"Skipping {skipped} already completed queries")
        queries = pending

    total = len(queries)
    print(
        f"Running {total} queries from dataset '{spec.name}' "
        f"(mode={mode}, concurrency={concurrency})"
    )

    # Build engine config
    kb_config = _build_knowledge_bases(spec, base_url)
    engine_config = {
        "retrieval": {"knowledge_bases": kb_config},
        "agents": _build_language_middleware(language),
    }

    # Select engine
    if mode == "fast":
        from by_qa.qa.engines.fast.engine import FastQAEngine

        engine_cls = FastQAEngine
    else:
        from by_qa.qa.engines.instant.engine import InstantQAEngine

        engine_cls = InstantQAEngine

    # Run queries concurrently, append to JSONL (with lock)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()
    counter_lock = asyncio.Lock()
    completed = 0
    errors = 0
    done_count = 0

    async def _run_one(query, index: int) -> None:
        nonlocal completed, errors, done_count
        async with sem:
            if show_tokens and concurrency == 1:
                print(f"\n[{index + 1}/{total}] {query.question[:80]}...", flush=True)
            result = await _run_single_query(
                engine_cls, engine_config, query, show_tokens and concurrency == 1
            )
            inf_result = InferenceResult.from_query_result(result)

            async with write_lock:
                with open(results_path, "a", encoding="utf-8") as out_f:
                    out_f.write(
                        json.dumps(inf_result.to_dict(), ensure_ascii=False) + "\n"
                    )
                    out_f.flush()

            async with counter_lock:
                done_count += 1
                if result.error:
                    errors += 1
                    status = f"ERROR: {result.error[:100]}"
                elif not result.answer:
                    errors += 1
                    status = "WARNING: empty answer"
                else:
                    completed += 1
                    status = "OK"
                print(
                    f"[{done_count}/{total}] qid={query.query_id} {status}", flush=True
                )

    tasks = [_run_one(query, i) for i, query in enumerate(queries)]
    await asyncio.gather(*tasks)

    print(f"\nInference complete: {completed} succeeded, {errors} errors")
    print(f"Results saved to: {results_path}")
    return results_path


async def run_judge(
    spec: DatasetSpec,
    mode: str,
    judge_model: str | None = None,
    results_path: Path | None = None,
    output_dir: Path | None = None,
) -> EvalReport:
    """Read inference results JSONL, judge each answer, and produce an EvalReport.

    Skips entries that already have a score (supports resume for judging).

    Args:
        spec: Dataset spec.
        mode: "instant" or "fast" (used in report metadata).
        judge_model: Override judge model type.
        results_path: Path to inference results JSONL.
        output_dir: Report output directory.

    Returns:
        EvalReport with full results.
    """
    if results_path is None:
        results_path = spec.data_dir / "inference_results.jsonl"

    if not results_path.exists():
        raise FileNotFoundError(
            f"Inference results not found at {results_path}. "
            f"Run 'python -m eval.cli run {spec.name}' first."
        )

    # Load all results from JSONL
    results: list[InferenceResult] = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            results.append(InferenceResult.from_dict(json.loads(line)))

    print(f"Loaded {len(results)} inference results from {results_path}")

    # Find entries that need judging (no score yet, and have a valid answer)
    pending = [r for r in results if r.score is None and r.answer and not r.error]
    already_judged = len(results) - len(pending)

    if not pending:
        print(f"All {len(results)} entries already judged")
    else:
        if already_judged:
            print(f"Skipping {already_judged} already judged entries")
        print(f"Judging {len(pending)} entries...")

        llm_service = LLMService()
        judge_model_type = judge_model or "generator"

        for i, r in enumerate(pending):
            verdict = await judge(
                llm_service,
                r.question,
                r.ground_truth,
                r.answer,
                model_type=judge_model_type,
            )
            verdict.query_id = r.query_id
            r.score = verdict.score
            r.reasoning = verdict.reasoning
            r.judge_model = verdict.judge_model

            score_label = "?" if verdict.score == -1 else str(verdict.score)
            print(f"  [{i + 1}/{len(pending)}] qid={r.query_id} score={score_label}")

        # Write back updated JSONL with scores
        results_path.write_text(
            "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in results)
            + "\n",
            encoding="utf-8",
        )
        print(f"Updated scores written to {results_path}")

    # Build report
    verdicts = [
        JudgeVerdict(
            query_id=r.query_id,
            score=r.score if r.score is not None else -1,
            reasoning=r.reasoning or "",
            judge_model=r.judge_model or "unknown",
        )
        for r in results
    ]

    correct = sum(1 for v in verdicts if v.score == 1)
    unscored = sum(1 for v in verdicts if v.score == -1)
    error_count = sum(1 for r in results if r.error)
    total_tokens = sum(r.tokens_used for r in results)
    total_latency = sum(r.latency_ms for r in results)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")

    report = EvalReport(
        dataset_name=spec.name,
        mode=mode,
        timestamp=timestamp,
        total_queries=len(results),
        correct=correct,
        accuracy=correct / len(results) if results else 0.0,
        total_tokens=total_tokens,
        total_latency_ms=total_latency,
        verdicts=verdicts,
        unscored_count=unscored,
        error_count=error_count,
    )

    # Save report JSON
    out_dir = output_dir or spec.data_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{spec.name}_{mode}_{timestamp}.json"
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Console summary
    print()
    print("=" * 60)
    print(f"Eval Report: {spec.name.upper()} ({mode})")
    print("=" * 60)
    print(f"Accuracy:   {correct}/{len(results)} ({report.accuracy:.1%})")
    print(f"Tokens:     {total_tokens:,}")
    avg_latency = total_latency / len(results) / 1000 if results else 0
    print(f"Latency:    {total_latency / 1000:.1f}s total, {avg_latency:.2f}s avg")
    if unscored:
        print(f"Unscored:   {unscored}")
    if error_count:
        print(f"Errors:     {error_count}")
    print("-" * 60)
    print(f"Report saved to: {report_path}")
    print("=" * 60)

    return report
