"""Agent-friendly retrieval API for instant-search workers."""

import asyncio
from typing import Any, Dict, List

from by_qa.core import post_discovered_json
from by_qa.core.logger import error, info
from by_qa.qa.instant.config import KnowledgeBaseConfig
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext


def _format_search_hit(item: dict[str, Any]) -> Dict[str, Any]:
    """Normalize remote API hits into the agent-facing retrieval shape."""
    file_path = item.get("filePath") or item.get("file_path", "")
    return {
        "content": item.get("chunkText") or item.get("chunk_text", ""),
        "source": file_path,
        "source_type": "knowledge_base",
        "score": item.get("score", 0.0),
        "kb_code": item.get("knCode") or item.get("kb_code"),
        "file_code": item.get("file_code"),
        "version": item.get("version"),
        "chunk_no": item.get("chunkNo") or item.get("chunk_no"),
        "source_code": item.get("source_code"),
        "type_code": item.get("type_code"),
        "file_path": file_path,
    }


def _format_search_error(
    *,
    service_name: str,
    path: str,
    kb_codes: list[str],
    exc: Exception,
) -> Dict[str, Any]:
    """Return retrieval-shaped metadata for a failed remote KB search."""
    return {
        "content": f"knowledge base search failed(service_name={service_name}, path={path}): {exc}",
        "source": f"{service_name}{path}",
        "source_type": "knowledge_base",
        "score": 0.0,
        "is_error": True,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "service_name": service_name,
        "path": path,
        "kb_codes": kb_codes,
    }


def _get_kb_field(knowledge_base: KnowledgeBaseConfig, field_name: str) -> Any:
    """Access knowledge-base config fields from the typed config object."""
    return getattr(knowledge_base, field_name)


async def _search_remote_knowledge_base(
    *,
    service_name: str,
    path: str,
    request_payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Call one discovered knowledge-base search API and return raw items."""
    request_kwargs: dict[str, Any] = {
        "service_name": service_name,
        "path": path,
        "json": request_payload,
    }
    if headers:
        request_kwargs["headers"] = headers
    payload = await post_discovered_json(**request_kwargs)
    response_data = payload.get("resultObject", {})
    items = response_data.get("data", [])
    if not isinstance(items, list):
        raise ValueError(
            "knowledge base search response resultObject.data must be a list"
        )
    return items


def _build_remote_search_requests(
    query: str,
    runtime_context: InstantSearchRuntimeContext,
) -> list[tuple[tuple[str, str], dict[str, str] | None, dict[str, Any]]]:
    """Build one API request per distinct service+path, grouped by kb_codes."""
    retrieval = runtime_context.retrieval
    grouped_kb_codes: dict[tuple[str, str], list[str]] = {}
    service_headers: dict[str, dict[str, str]] = {}

    for knowledge_base in retrieval.knowledge_bases:
        kb_code = _get_kb_field(knowledge_base, "kb_code")
        service_name = _get_kb_field(knowledge_base, "service_name")
        path = _get_kb_field(knowledge_base, "path")
        headers = _get_kb_field(knowledge_base, "headers")
        if not kb_code or not service_name or not path:
            continue
        normalized_headers = dict(headers) if headers else None
        if normalized_headers:
            # Headers are service-level credentials/options. The same service_name
            # may expose multiple APIs, so collect headers once per service and
            # apply the merged result to every request for that service. If a
            # duplicate header key appears, the later config value wins.
            service_headers.setdefault(service_name, {}).update(normalized_headers)
        group_key = (service_name, path)
        grouped_codes = grouped_kb_codes.setdefault(group_key, [])
        if kb_code not in grouped_codes:
            grouped_codes.append(kb_code)

    requests: list[tuple[tuple[str, str], dict[str, str] | None, dict[str, Any]]] = []
    for (service_name, path), kb_codes in grouped_kb_codes.items():
        requests.append(
            (
                (service_name, path),
                service_headers.get(service_name) or None,
                {
                    "query": query,
                    "knCodeList": kb_codes,
                    "topK": retrieval.top_k,
                    "searchMode": "mixedRecall",
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
            _search_remote_knowledge_base(
                service_name=service_name,
                path=path,
                request_payload=request_payload,
                headers=headers,
            )
            for (service_name, path), headers, request_payload in requests
        ],
        return_exceptions=True,
    )

    aggregated_results: list[dict[str, Any]] = []
    for ((service_name, path), headers, request_payload), items in zip(
        requests, responses
    ):
        del headers
        if isinstance(items, Exception):
            error(
                "[instant_search.retrieval] remote KB search failed: service_name=%s, path=%s, kb_codes=%s, error=%s",
                service_name,
                path,
                request_payload["knCodeList"],
                items,
            )
            aggregated_results.append(
                _format_search_error(
                    service_name=service_name,
                    path=path,
                    kb_codes=request_payload["knCodeList"],
                    exc=items,
                )
            )
            continue
        aggregated_results.extend(_format_search_hit(item) for item in items)

    aggregated_results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return aggregated_results


__all__ = ["search_knowledge_items"]
