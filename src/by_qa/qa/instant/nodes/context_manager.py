"""Context manager node for instant-search graph."""

from collections import defaultdict
from typing import Dict, List, Tuple

from langchain_core.messages import SystemMessage

from by_qa.config import get_settings
from by_qa.core.logger import info
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.state import InstantSearchState, MultiHopState, SingleHopState

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None  # type: ignore[assignment,misc]


def estimate_tokens(text: str) -> int:
    """Estimate token count for text."""
    if not text:
        return 0
    return int(len(text) * 1.3)


def calculate_available_tokens(model_max_tokens: int) -> int:
    """Calculate available token budget for retrieval results."""
    settings = get_settings()
    ratio = settings.instant_search_max_context_ratio
    total_budget = int(model_max_tokens * ratio)
    reserved = settings.instant_search_reserved_tokens
    return max(0, total_budget - reserved)


def truncate_at_sentence_boundary(text: str, max_tokens: int) -> str:
    """Truncate text at sentence boundary."""
    if not text:
        return ""
    sentence_endings = ["，", "。", "！", "？", ",", ".", "!", "?", "\n\n", "\n"]
    if estimate_tokens(text) <= max_tokens:
        return text
    best_end = 0
    for i, char in enumerate(text):
        if char in sentence_endings:
            segment = text[: i + 1]
            if estimate_tokens(segment) <= max_tokens:
                best_end = i + 1
    return text[:best_end] if best_end > 0 else ""


def truncate_single_result(result: Dict, max_tokens: int) -> Dict:
    """Truncate a single result at sentence boundary."""
    content = result.get("content", "")
    truncated_content = truncate_at_sentence_boundary(content, max_tokens)
    if truncated_content and len(truncated_content) < len(content):
        result = result.copy()
        result["content"] = truncated_content
        result["truncated"] = True
        result["token_count"] = estimate_tokens(truncated_content)
        return result
    return result


def group_results_by_key(results: List[Dict]) -> Dict[Tuple[str, str], List[Dict]]:
    """Group results by (sub_query_id, source_type)."""
    groups = defaultdict(list)
    for result in results:
        sub_query_id = result.get("sub_query_id", "unknown")
        source_type = result.get("source_type", "unknown")
        groups[(sub_query_id, source_type)].append(result)
    for key in groups:
        groups[key].sort(key=lambda r: r.get("score", 0), reverse=True)
    return dict(groups)


def truncate_retrieval_results_round_robin(
    results: List[Dict], available_tokens: int, min_sentence_tokens: int = None
) -> Tuple[List[Dict], List[str]]:
    """Truncate retrieval results using Round-Robin strategy."""
    settings = get_settings()
    min_sentence_tokens = (
        min_sentence_tokens or settings.instant_search_min_sentence_tokens
    )
    quality_results = results
    if not quality_results:
        return [], []
    groups = group_results_by_key(quality_results)
    num_groups = len(groups)
    if num_groups == 0:
        return [], []
    info(f"[ContextManager] Grouped into {num_groups} groups: {list(groups.keys())}")
    selected_results = []
    truncated_ids = []
    current_tokens = 0
    group_pointers = {key: 0 for key in groups}
    round_num = 0
    while current_tokens < available_tokens:
        any_selected = False
        for key in groups:
            pointer = group_pointers[key]
            group_results = groups[key]
            if pointer >= len(group_results):
                continue
            result = group_results[pointer]
            result_tokens = result.get(
                "token_count", estimate_tokens(result.get("content", ""))
            )
            if current_tokens + result_tokens <= available_tokens:
                selected_results.append(result)
                current_tokens += result_tokens
                group_pointers[key] = pointer + 1
                any_selected = True
            else:
                remaining = available_tokens - current_tokens
                if remaining >= min_sentence_tokens:
                    truncated = truncate_single_result(result, remaining)
                    if truncated.get("truncated"):
                        selected_results.append(truncated)
                        current_tokens += truncated.get(
                            "token_count", estimate_tokens(truncated.get("content", ""))
                        )
                        group_pointers[key] = pointer + 1
                        any_selected = True
                    else:
                        truncated_ids.append(result.get("sub_query_id", "unknown"))
                        group_pointers[key] = pointer + 1
                else:
                    truncated_ids.append(result.get("sub_query_id", "unknown"))
                    group_pointers[key] = pointer + 1
        if not any_selected:
            break
        round_num += 1
        if round_num > 100:
            break
    for key in groups:
        pointer = group_pointers[key]
        group_results = groups[key]
        for i in range(pointer, len(group_results)):
            truncated_ids.append(group_results[i].get("sub_query_id", "unknown"))
    seen = set()
    truncated_ids_unique = []
    for tid in truncated_ids:
        if tid not in seen:
            seen.add(tid)
            truncated_ids_unique.append(tid)
    info(
        f"[ContextManager] Selected {len(selected_results)} results ({current_tokens} tokens), "
        f"truncated {len(truncated_ids_unique)} queries, {num_groups} groups balanced"
    )
    return selected_results, truncated_ids_unique


def build_context_for_llm(retrieval_results: List[Dict]) -> str:
    """Build context string for LLM."""
    if not retrieval_results:
        return "未找到相关检索结果。"
    results_by_query: Dict[str, Dict[str, List[Dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for result in retrieval_results:
        qid = result.get("sub_query_id", "unknown")
        source_type = result.get("source_type", "unknown")
        results_by_query[qid][source_type].append(result)
    context_parts = []
    for qid, sources in results_by_query.items():
        all_results = []
        for src_results in sources.values():
            all_results.extend(src_results)
        sub_query_text = (
            all_results[0].get("sub_query_text", f"子查询 {qid}")
            if all_results
            else f"子查询 {qid}"
        )
        context_parts.append(f"## 子查询: {sub_query_text}\n")
        if "knowledge_base" in sources:
            kb_results = sources["knowledge_base"]
            context_parts.append("[知识库]")
            for i, result in enumerate(kb_results, 1):
                content = result.get("content", "")
                truncated_marker = " (已截断)" if result.get("truncated") else ""
                context_parts.append(f"{i}. {content}{truncated_marker}")
            context_parts.append("")
        if "web" in sources:
            web_results = sources["web"]
            context_parts.append("[联网检索]")
            for i, result in enumerate(web_results, 1):
                source = result.get("source", "")
                if "(" in source and source.endswith(")"):
                    title = source[: source.rfind("(")].strip()
                    url = source[source.rfind("(") + 1 : -1].strip()
                else:
                    title = source
                    url = ""
                content = result.get("content", "")
                truncated_marker = " (已截断)" if result.get("truncated") else ""
                context_parts.append(f"{i}. {title}({url})")
                context_parts.append(f"{content}{truncated_marker}")
            context_parts.append("")
    return "\n".join(context_parts)


async def context_manager_node(
    state: InstantSearchState | SingleHopState | MultiHopState,
    runtime: Runtime[InstantSearchRuntimeContext] = None,
) -> dict:
    """Context manager node for instant search."""
    if (
        runtime is None
        or runtime.context is None
        or runtime.context.llm_service is None
    ):
        raise RuntimeError(
            "llm_service is required in runtime context for context_manager_node"
        )
    generator_config = await runtime.context.llm_service._provider.get_config(
        "generator"
    )
    if generator_config.max_model_len is None:
        raise RuntimeError(
            "GENERATOR_MAX_MODEL_LEN is required for context_manager_node"
        )
    total_results = len(state["retrieval_results"])
    info(f"[context_manager] Managing context for {total_results} results")
    available_tokens = calculate_available_tokens(generator_config.max_model_len)
    truncated_results, truncated_ids_unused = truncate_retrieval_results_round_robin(
        state["retrieval_results"], available_tokens
    )
    del truncated_ids_unused
    context = build_context_for_llm(truncated_results)
    total_tokens = estimate_tokens(context)
    info(
        f"[context_manager] Truncated to {len(truncated_results)} results, {total_tokens} tokens"
    )
    return {
        "retrieval_results": {"mode": "RESET", "data": truncated_results},
        "messages": [
            SystemMessage(
                content=f"上下文已优化：{len(truncated_results)} 条结果，{total_tokens} tokens"
            ),
        ],
    }


__all__ = [
    "build_context_for_llm",
    "calculate_available_tokens",
    "context_manager_node",
    "estimate_tokens",
    "group_results_by_key",
    "truncate_at_sentence_boundary",
    "truncate_retrieval_results_round_robin",
    "truncate_single_result",
]
