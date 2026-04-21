# src/by_qa/qa/instant/runtime/dispatcher.py
"""ServiceToolDispatcher: generates LangGraph tools from KnowledgeBaseConfig.operations."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain.tools import ToolRuntime, tool

from by_qa.core import post_discovered_json
from by_qa.core.logger import error, info
from by_qa.qa.instant.config import KnowledgeBaseConfig
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.operation_registry import (
    OPERATION_REGISTRY,
    OperationSpec,
    OperationType,
)


def _format_search_hit(item: dict[str, Any]) -> dict[str, Any]:
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


def _format_error(*, service_name: str, path: str, exc: Exception) -> dict[str, Any]:
    return {
        "content": f"Tool call failed — {exc}",
        "source": f"{service_name}{path}",
        "source_type": "knowledge_base",
        "score": 0.0,
        "is_error": True,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "service_name": service_name,
        "path": path,
    }


class KnowledgeBaseNotFoundError(Exception):
    pass


class KnowledgeBaseAccessDeniedError(Exception):
    pass


class OperationNotSupportedError(Exception):
    pass


class ServiceToolDispatcher:
    """Generates LangGraph tools from KnowledgeBaseConfig.operations at graph-build time."""

    def __init__(self, knowledge_bases: list[KnowledgeBaseConfig]) -> None:
        self._knowledge_bases = knowledge_bases
        self._supported_ops: set[OperationType] = set()
        for kb in knowledge_bases:
            for op_key in kb.operations:
                if isinstance(op_key, OperationType):
                    if op_key in OPERATION_REGISTRY:
                        self._supported_ops.add(op_key)
                else:
                    try:
                        op_type = OperationType(op_key)
                        if op_type in OPERATION_REGISTRY:
                            self._supported_ops.add(op_type)
                    except ValueError:
                        pass

    def build_tools(self) -> list[Any]:
        return [self._make_tool(OPERATION_REGISTRY[op]) for op in self._supported_ops]

    def _make_tool(self, spec: OperationSpec) -> Any:
        dispatcher = self

        async def _fn(
            runtime: ToolRuntime[InstantSearchRuntimeContext], **kwargs: Any
        ) -> list[dict[str, Any]]:
            return await dispatcher._dispatch(
                spec.operation_type, kwargs, runtime.context
            )

        _fn.__name__ = spec.tool_name
        _fn.__doc__ = spec.description
        return tool(_fn, args_schema=spec.input_schema)

    async def _dispatch(
        self,
        operation_type: OperationType,
        payload: dict[str, Any],
        runtime_context: InstantSearchRuntimeContext,
    ) -> list[dict[str, Any]]:
        if operation_type == OperationType.SEARCH:
            return await self._dispatch_search(payload, runtime_context)
        return await self._dispatch_single_kb(operation_type, payload, runtime_context)

    async def _dispatch_search(
        self,
        payload: dict[str, Any],
        runtime_context: InstantSearchRuntimeContext,
    ) -> list[dict[str, Any]]:
        kbs = runtime_context.retrieval.knowledge_bases
        authorized_codes = {kb.kb_code for kb in kbs}
        kn_code_list: list[str] | None = payload.get("knCodeList") or payload.get(
            "kn_code_list"
        )

        error_results: list[dict[str, Any]] = []
        if kn_code_list:
            unauthorized = [
                code for code in kn_code_list if code not in authorized_codes
            ]
            for code in unauthorized:
                exc = KnowledgeBaseAccessDeniedError(
                    f"Access denied: knowledge base '{code}' is not in the authorized KB list."
                )
                error("[dispatcher] search: %s", exc)
                error_results.append(_format_error(service_name="", path="", exc=exc))
            kbs = [kb for kb in kbs if kb.kb_code in kn_code_list]

        grouped: dict[tuple[str, str], list[str]] = {}
        service_headers: dict[str, dict[str, str]] = {}
        for kb in kbs:
            path = kb.operations.get(OperationType.SEARCH)
            if not path:
                continue
            if kb.headers:
                service_headers.setdefault(kb.service_name, {}).update(kb.headers)
            key = (kb.service_name, path)
            grouped.setdefault(key, [])
            if kb.kb_code not in grouped[key]:
                grouped[key].append(kb.kb_code)

        if not grouped:
            return error_results

        top_k = runtime_context.retrieval.top_k
        requests = [
            (
                service_name,
                path,
                service_headers.get(service_name),
                {
                    "query": payload["query"],
                    "knCodeList": kb_codes,
                    "topK": top_k,
                    "searchMode": "mixedRecall",
                },
            )
            for (service_name, path), kb_codes in grouped.items()
        ]

        info("[dispatcher] search: dispatching %s requests", len(requests))
        responses = await asyncio.gather(
            *[
                post_discovered_json(
                    service_name=sn,
                    path=p,
                    json=body,
                    **({} if not h else {"headers": h}),
                )
                for sn, p, h, body in requests
            ],
            return_exceptions=True,
        )

        results: list[dict[str, Any]] = []
        for (sn, p, h, body), resp in zip(requests, responses):
            if isinstance(resp, Exception):
                error(
                    "[dispatcher] search failed: service=%s path=%s error=%s",
                    sn,
                    p,
                    resp,
                )
                results.append(_format_error(service_name=sn, path=p, exc=resp))
                continue
            for item in resp.get("resultObject", {}).get("data", []):
                results.append(_format_search_hit(item))

        results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return error_results + results

    async def _dispatch_single_kb(
        self,
        operation_type: OperationType,
        payload: dict[str, Any],
        runtime_context: InstantSearchRuntimeContext,
    ) -> list[dict[str, Any]]:
        kn_code = payload.get("knCode") or payload.get("kn_code", "")
        kb = next(
            (
                kb
                for kb in runtime_context.retrieval.knowledge_bases
                if kb.kb_code == kn_code
            ),
            None,
        )
        if kb is None:
            authorized_codes = [
                k.kb_code for k in runtime_context.retrieval.knowledge_bases
            ]
            exc = KnowledgeBaseAccessDeniedError(
                f"Access denied: knowledge base '{kn_code}' is not in the authorized KB list. "
                f"Authorized KB codes: {authorized_codes}"
            )
            error("[dispatcher] %s: %s", operation_type.value, exc)
            return [_format_error(service_name="", path="", exc=exc)]

        path = kb.operations.get(operation_type)
        if not path:
            supported = [op.value for op in kb.operations]
            exc = OperationNotSupportedError(
                f"KB '{kn_code}' does not support '{operation_type.value}'. "
                f"Supported operations: {supported}"
            )
            error("[dispatcher] %s: %s", operation_type.value, exc)
            return [_format_error(service_name=kb.service_name, path="", exc=exc)]

        headers = dict(kb.headers) if kb.headers else None
        kwargs: dict[str, Any] = {
            "service_name": kb.service_name,
            "path": path,
            "json": payload,
        }
        if headers:
            kwargs["headers"] = headers

        try:
            resp = await post_discovered_json(**kwargs)
            data = resp.get("resultObject", {}).get("data", [])
            if not isinstance(data, list):
                data = [data]
            return data
        except Exception as exc:
            error(
                "[dispatcher] %s failed: service=%s path=%s error=%s",
                operation_type.value,
                kb.service_name,
                path,
                exc,
            )
            return [_format_error(service_name=kb.service_name, path=path, exc=exc)]


__all__ = [
    "KnowledgeBaseAccessDeniedError",
    "KnowledgeBaseNotFoundError",
    "OperationNotSupportedError",
    "ServiceToolDispatcher",
]
