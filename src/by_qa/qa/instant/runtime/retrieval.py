"""Agent-friendly retrieval API for instant-search workers."""

import asyncio
from typing import Any, Dict, List

import httpx

from by_qa.core.logger import error, info
from by_qa.qa.instant.config import KnowledgeBaseConfig
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext

DEFAULT_REMOTE_SEARCH_TIMEOUT = 30.0


def _format_search_hit(item: dict[str, Any]) -> Dict[str, Any]:
    """Normalize remote API hits into the agent-facing retrieval shape."""
    file_path = item.get("file_path", "")
    return {
        "content": item.get("chunk_text", ""),
        "source": file_path,
        "source_type": "knowledge_base",
        "score": item.get("score", 0.0),
        "kb_code": item.get("kb_code"),
        "file_code": item.get("file_code"),
        "version": item.get("version"),
        "chunk_no": item.get("chunk_no"),
        "source_code": item.get("source_code"),
        "type_code": item.get("type_code"),
        "file_path": file_path,
    }


def _get_kb_field(knowledge_base: KnowledgeBaseConfig, field_name: str) -> Any:
    """Access knowledge-base config fields from the typed config object."""
    return getattr(knowledge_base, field_name)


async def _search_remote_knowledge_base(
    *, url: str, request_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Call one remote knowledge-base search API and return raw items."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, json=request_payload, timeout=DEFAULT_REMOTE_SEARCH_TIMEOUT
        )
        response.raise_for_status()

    payload = response.json()
    response_data = payload.get("data", {})
    items = response_data.get("items", [])
    if not isinstance(items, list):
        raise ValueError("knowledge base search response data.items must be a list")
    return items


def _build_remote_search_requests(
    query: str,
    runtime_context: InstantSearchRuntimeContext,
) -> list[tuple[str, dict[str, Any]]]:
    """Build one API request per distinct kb_url, grouped by kb_codes."""
    retrieval = runtime_context.retrieval
    grouped_kb_codes: dict[str, list[str]] = {}

    for knowledge_base in retrieval.knowledge_bases:
        kb_code = _get_kb_field(knowledge_base, "kb_code")
        kb_url = _get_kb_field(knowledge_base, "kb_url")
        if not kb_code or not kb_url:
            continue
        grouped_codes = grouped_kb_codes.setdefault(kb_url, [])
        if kb_code not in grouped_codes:
            grouped_codes.append(kb_code)

    requests: list[tuple[str, dict[str, Any]]] = []
    for kb_url, kb_codes in grouped_kb_codes.items():
        requests.append(
            (
                kb_url,
                {
                    "query": query,
                    "kb_codes": kb_codes,
                    "source_codes": retrieval.source_codes,
                    "type_codes": retrieval.type_codes,
                    "top_k": retrieval.top_k,
                    "vector_top_k": retrieval.vector_top_k,
                    "text_top_k": retrieval.text_top_k,
                },
            )
        )
    return requests


async def search_knowledge_items(
    query: str,
    runtime_context: InstantSearchRuntimeContext,
) -> List[Dict[str, Any]]:
    """Search KB chunks through remote knowledge-base APIs and merge the results."""
    requests = _build_remote_search_requests(query, runtime_context)
    if not requests:
        return []

    info(
        "[instant_search.retrieval] dispatching remote KB search: request_count=%s",
        len(requests),
    )

    responses = await asyncio.gather(
        *[
            _search_remote_knowledge_base(url=kb_url, request_payload=request_payload)
            for kb_url, request_payload in requests
        ],
        return_exceptions=True,
    )

    aggregated_results: list[dict[str, Any]] = []
    for (kb_url, request_payload), items in zip(requests, responses):
        if isinstance(items, Exception):
            error(
                "[instant_search.retrieval] remote KB search failed: url=%s, kb_codes=%s, error=%s",
                kb_url,
                request_payload["kb_codes"],
                items,
            )
            continue
        aggregated_results.extend(_format_search_hit(item) for item in items)

    aggregated_results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return aggregated_results


__all__ = ["search_knowledge_items"]
