"""Core eval loop: load queries -> run engine -> judge -> report."""

from __future__ import annotations

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
from eval.models import EvalReport, JudgeVerdict, QueryResult

if TYPE_CHECKING:
    from eval.datasets.base import DatasetSpec


def _build_knowledge_bases(spec: DatasetSpec) -> list[dict]:
    kb_code = spec.get_kb_code()
    kb_config = spec.kb_config
    return [
        {
            "kb_code": kb_code,
            "kb_name": spec.get_kb_name(),
            "kb_description": "",
            "service_name": kb_config.kb_service_name,
            "operations": {
                OperationType.KNOWLEDGE_SEARCH: kb_config.kb_search_url,
            },
            **({"base_url": kb_config.kb_base_url} if kb_config.kb_base_url else {}),
        }
    ]


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
    """Run a single query through the engine and collect results."""
    request = CoreInput(query=query.question, session_id=str(uuid.uuid4()))
    start = time.perf_counter()
    tokens = 0
    answer = ""
    error_msg: str | None = None

    try:
        async with engine_class(config=config) as engine:
            async for event in engine.stream_search(request):
                if event.type == StreamEventType.ERROR:
                    error_msg = event.data.get("error", str(event))
                if event.type == StreamEventType.TOKEN:
                    content = event.data.get("content", "")
                    tokens += 1
                    if show_tokens and content:
                        print(content, end="", flush=True)
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


async def run_eval(
    spec: DatasetSpec,
    mode: str,
    language: str | None = None,
    show_tokens: bool = False,
    sample: int | None = None,
    judge_model: str | None = None,
    query_ids: list[str] | None = None,
    output_dir: Path | None = None,
) -> EvalReport:
    """Run full evaluation on a dataset.

    Args:
        spec: Dataset spec providing queries and KB config.
        mode: "instant" or "fast".
        language: Optional output language.
        show_tokens: Print token stream.
        sample: Run only N queries.
        judge_model: Override judge model type.
        query_ids: Run only specific query IDs.
        output_dir: Report output directory.

    Returns:
        EvalReport with full results.
    """
    # Load queries
    if sample is not None:
        queries = spec.load_queries_sample(sample)
    else:
        queries = spec.load_queries()

    if query_ids:
        id_set = set(query_ids)
        queries = [q for q in queries if q.query_id in id_set]
        if not queries:
            raise ValueError("No queries matched the given --query-ids filter")

    total = len(queries)
    print(f"Running {total} queries from dataset '{spec.name}' (mode={mode})")
    if sample and sample < len(spec.load_queries()):
        print(f"  (sampled {sample} of {len(spec.load_queries())} total)")

    # Build engine config
    kb_config = _build_knowledge_bases(spec)
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

    # Run queries (sequential)
    results: list[QueryResult] = []
    for i, query in enumerate(queries):
        print(f"\n[{i + 1}/{total}] {query.question[:80]}...", flush=True)
        result = await _run_single_query(engine_cls, engine_config, query, show_tokens)
        results.append(result)
        if result.error:
            print(f"  ERROR: {result.error[:100]}")
        elif not result.answer:
            print("  WARNING: empty answer")

    # Judge
    print(f"\nJudging {len(results)} results...")
    llm_service = LLMService()
    judge_model_type = judge_model or "generator"

    verdicts: list[JudgeVerdict] = []
    unscored = 0
    for i, result in enumerate(results):
        if result.error or not result.answer:
            verdicts.append(
                JudgeVerdict(
                    query_id=result.query.query_id,
                    score=0,
                    reasoning=f"Error: {result.error}"
                    if result.error
                    else "Empty answer",
                    judge_model="system",
                )
            )
            continue

        verdict = await judge(
            llm_service,
            result.query.question,
            result.query.ground_truth,
            result.answer,
            model_type=judge_model_type,
        )
        verdict.query_id = result.query.query_id
        verdicts.append(verdict)
        if verdict.score == -1:
            unscored += 1

        # Progress indicator
        score_label = "?" if verdict.score == -1 else str(verdict.score)
        print(
            f"  [{i + 1}/{len(results)}] qid={result.query.query_id} score={score_label}"
        )

    # Build report
    correct = sum(1 for v in verdicts if v.score == 1)
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

    # Save to file
    out_dir = output_dir or Path("eval/reports")
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
    print(
        f"Latency:    {total_latency / 1000:.1f}s total, {total_latency / len(results) / 1000:.2f}s avg"
    )
    if unscored:
        print(f"Unscored:   {unscored}")
    if error_count:
        print(f"Errors:     {error_count}")
    print("-" * 60)
    print(f"Report saved to: {report_path}")
    print("=" * 60)

    return report
