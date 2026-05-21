"""KnowledgeSearchOperation — parallel search across multiple knowledge bases."""

from __future__ import annotations

from typing import Any

from by_qa.core.exceptions import KnowledgeBaseNotFoundOrForbiddenError
from by_qa.core.logger import error, info
from by_qa.qa.common.config import KnowledgeBaseConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.tools.operations.base import (
    BaseOperation,
    DispatchRequest,
    _normalize_headers,
)


def _format_search_result(item: dict[str, Any]) -> dict[str, Any]:
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
    *, service_name: str, path: str, exc: Exception
) -> dict[str, Any]:
    return {
        "content": f"Search failed — {exc}",
        "source": f"{service_name}{path}",
        "source_type": "knowledge_base",
        "score": 0.0,
        "is_error": True,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "service_name": service_name,
        "path": path,
    }


def _format_search_api_error(
    *, service_name: str, path: str, result_msg: str
) -> dict[str, Any]:
    return {
        "content": f"Search API error — {result_msg}",
        "source": f"{service_name}{path}",
        "source_type": "knowledge_base",
        "score": 0.0,
        "is_error": True,
        "error": result_msg,
        "error_type": "ApiError",
        "service_name": service_name,
        "path": path,
    }


class KnowledgeSearchOperation(BaseOperation):
    """Parallel knowledge-base search across multiple KBs grouped by service."""

    operation_type = OperationType.KNOWLEDGE_SEARCH

    def build_requests(
        self,
        payload: dict[str, Any],
        kbs: list[KnowledgeBaseConfig],
        ctx: QARuntimeContext,
    ) -> tuple[list[DispatchRequest], list[dict[str, Any]]]:
        authorized_codes = {kb.kb_code for kb in kbs}
        kn_code_list: list[str] | None = payload.get("kn_code_list") or payload.get(
            "knCodeList"
        )

        pre_dispatch_errors: list[dict[str, Any]] = []
        if kn_code_list:
            unauthorized = [
                code for code in kn_code_list if code not in authorized_codes
            ]
            for code in unauthorized:
                exc = KnowledgeBaseNotFoundOrForbiddenError(
                    f"Knowledge base '{code}' not found or access not permitted."
                )
                error("[dispatcher] search: %s", exc)
                pre_dispatch_errors.append(
                    _format_search_error(service_name="", path="", exc=exc)
                )
            kbs = [kb for kb in kbs if kb.kb_code in kn_code_list]

        # Group KBs by (service_name, path, base_url)
        grouped: dict[tuple[str, str, str | None], list[str]] = {}
        service_headers: dict[str, dict[str, str]] = {}
        for kb in kbs:
            path = kb.operations.get(OperationType.KNOWLEDGE_SEARCH)
            if not path:
                continue
            normalized = _normalize_headers(kb.headers)
            if normalized:
                service_headers.setdefault(kb.service_name, {}).update(normalized)
            key = (kb.service_name, path, kb.base_url)
            grouped.setdefault(key, [])
            if kb.kb_code not in grouped[key]:
                grouped[key].append(kb.kb_code)

        if not grouped:
            return ([], pre_dispatch_errors)

        top_k = ctx.retrieval.top_k
        requests = [
            DispatchRequest(
                service_name=service_name,
                path=path,
                base_url=base_url,
                headers=service_headers.get(service_name),
                body={
                    "query": payload["query"],
                    "knCodeList": kb_codes,
                    "topK": top_k,
                    "searchMode": "mixedRecall",
                },
            )
            for (service_name, path, base_url), kb_codes in grouped.items()
        ]

        for r in requests:
            if r.base_url:
                info(
                    "[dispatcher] search: direct mode url=%s%s",
                    r.base_url.rstrip("/"),
                    "/" + r.path.lstrip("/"),
                )
            else:
                info(
                    "[dispatcher] search: discovery mode service=%s path=%s",
                    r.service_name,
                    r.path,
                )
        info("[dispatcher] search: dispatching %s requests", len(requests))

        return (requests, pre_dispatch_errors)

    def process_response(
        self, response: dict[str, Any], request: DispatchRequest
    ) -> list[dict[str, Any]]:
        return [
            _format_search_result(item)
            for item in response.get("resultObject", {}).get("data", [])
        ]

    def process_api_error(
        self, response: dict[str, Any], request: DispatchRequest
    ) -> list[dict[str, Any]]:
        result_msg = response.get("resultMsg", "unknown error")
        error(
            "[dispatcher] search API error: service=%s base_url=%s path=%s resultMsg=%s",
            request.service_name,
            request.base_url,
            request.path,
            result_msg,
        )
        return [
            _format_search_api_error(
                service_name=request.service_name,
                path=request.path,
                result_msg=result_msg,
            )
        ]

    def process_error(
        self, exc: Exception, request: DispatchRequest
    ) -> list[dict[str, Any]]:
        error(
            "[dispatcher] search failed: service=%s base_url=%s path=%s error=%s",
            request.service_name,
            request.base_url,
            request.path,
            exc,
        )
        return [
            _format_search_error(
                service_name=request.service_name, path=request.path, exc=exc
            )
        ]

    def aggregate(self, parts: list[Any]) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for part in parts:
            if isinstance(part, list):
                for item in part:
                    if item.get("is_error"):
                        errors.append(item)
                    else:
                        results.append(item)
            elif isinstance(part, dict):
                if part.get("is_error"):
                    errors.append(part)
                else:
                    results.append(part)
        results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return errors + results


__all__ = ["KnowledgeSearchOperation"]
