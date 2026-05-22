"""Knowledge-base tool builders and tool-call middleware."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from langgraph.typing import ContextT, StateT
from pydantic import ConfigDict

from by_qa.core import logger, post_discovered_json
from by_qa.core.exceptions import (
    KnowledgeBaseNotFoundOrForbiddenError,
    OperationNotSupportedError,
)
from by_qa.core.logger import error, info
from by_qa.qa.common.config import KnowledgeBaseConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.operation_registry import (
    OPERATION_REGISTRY,
    OperationSpec,
    OperationType,
)
from by_qa.qa.tools.dsl_guide import get_dsl_guide
from by_qa.qa.tools.operations.base import (
    BaseOperation,
    DispatchRequest,
    _normalize_headers,
)
from by_qa.qa.tools.operations.knowledge_search import KnowledgeSearchOperation
from by_qa.qa.tools.operations.metadata_fields_list import MetadataFieldsListOperation


def _format_operation_error(
    *,
    operation_type: OperationType,
    service_name: str,
    path: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "resultCode": "-1",
        "resultMsg": str(exc),
        "resultObject": {},
        "is_error": True,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "operation_type": operation_type.value,
        "service_name": service_name,
        "path": path,
    }


async def _post_direct_json(
    *,
    base_url: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST JSON directly to base_url + path, bypassing service discovery."""
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=json_body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("response body must be a JSON object")
    return data


class ServiceToolDispatcher:
    """Generates LangGraph tools and dispatches operations to knowledge-base services.

    Parallel-dispatch operations (like KNOWLEDGE_SEARCH) are delegated to
    BaseOperation subclasses for request building and result processing.
    Single-KB operations (LIST_DIR, GLOB, READ_FILE) use the built-in simple path.
    """

    # Mapping from OperationType to BaseOperation subclass for parallel dispatch.
    _PARALLEL_OP_CLASSES: dict[OperationType, type[BaseOperation]] = {
        OperationType.KNOWLEDGE_SEARCH: KnowledgeSearchOperation,
        OperationType.METADATA_FIELDS_LIST: MetadataFieldsListOperation,
    }

    def __init__(
        self,
        knowledge_bases: list[KnowledgeBaseConfig],
    ) -> None:
        self._knowledge_bases = knowledge_bases

        # Discover supported ops from KB configs
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

        # Auto-register parallel-dispatch operations
        self._parallel_ops: dict[OperationType, BaseOperation] = {}
        for op_type in self._supported_ops:
            op_cls = self._PARALLEL_OP_CLASSES.get(op_type)
            if op_cls is not None:
                self._parallel_ops[op_type] = op_cls()

    def build_tools(self) -> list[Any]:
        tools = [self._make_tool(OPERATION_REGISTRY[op]) for op in self._supported_ops]
        if OperationType.METADATA_FIELDS_LIST in self._supported_ops:
            tools.append(get_dsl_guide)
        return tools

    async def dispatch(
        self,
        operation_type: OperationType,
        payload: dict[str, Any],
        runtime_context: QARuntimeContext,
    ) -> Any:
        """Dispatch an operation.

        Uses a registered BaseOperation for parallel dispatch, or falls back
        to the simple single-KB path.
        """
        logger.info(
            "[dispatcher] dispatch: operation_type=%s payload=%s",
            operation_type,
            payload,
        )
        parallel_op = self._parallel_ops.get(operation_type)
        if parallel_op is not None:
            return await self._dispatch_parallel(parallel_op, payload, runtime_context)
        return await self._dispatch_single_kb(operation_type, payload, runtime_context)

    def _make_tool(self, spec: OperationSpec) -> Any:
        dispatcher = self

        extended_schema = type(
            spec.input_schema.__name__,
            (spec.input_schema,),
            {"model_config": ConfigDict(extra="allow", populate_by_name=True)},
        )

        async def _fn(runtime: ToolRuntime[QARuntimeContext], **kwargs: Any) -> str:
            camel_payload = spec.input_schema.model_validate(kwargs).model_dump(
                by_alias=True, exclude_none=True
            )
            results = await dispatcher.dispatch(
                spec.operation_type, camel_payload, runtime.context
            )
            return json.dumps(results, ensure_ascii=False)

        _fn.__name__ = spec.tool_name
        _fn.__doc__ = spec.description
        return tool(_fn, args_schema=extended_schema)

    async def _execute_request(self, request: DispatchRequest) -> dict[str, Any]:
        """Execute a single HTTP request (direct URL or service discovery)."""
        if request.base_url:
            return await _post_direct_json(
                base_url=request.base_url,
                path=request.path,
                json_body=request.body,
                headers=request.headers,
            )
        kwargs: dict[str, Any] = {
            "service_name": request.service_name,
            "path": request.path,
            "json": request.body,
        }
        if request.headers:
            kwargs["headers"] = request.headers
        return await post_discovered_json(**kwargs)

    async def _dispatch_parallel(
        self,
        op: BaseOperation,
        payload: dict[str, Any],
        runtime_context: QARuntimeContext,
    ) -> Any:
        """Dispatch a BaseOperation across multiple KBs in parallel."""
        kbs = runtime_context.retrieval.knowledge_bases
        requests, pre_dispatch_errors = op.build_requests(payload, kbs, runtime_context)

        if not requests:
            return op.aggregate(pre_dispatch_errors)

        responses = await asyncio.gather(
            *[self._execute_request(r) for r in requests],
            return_exceptions=True,
        )

        all_parts: list[Any] = list(pre_dispatch_errors)
        for req, resp in zip(requests, responses):
            if isinstance(resp, Exception):
                all_parts.append(op.process_error(resp, req))
            elif resp.get("resultCode") != "0":
                all_parts.append(op.process_api_error(resp, req))
            else:
                all_parts.append(op.process_response(resp, req))

        return op.aggregate(all_parts)

    async def _dispatch_single_kb(
        self,
        operation_type: OperationType,
        payload: dict[str, Any],
        runtime_context: QARuntimeContext,
    ) -> dict[str, Any]:
        kn_code = payload.get("kn_code") or payload.get("knCode", "")
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
            exc = KnowledgeBaseNotFoundOrForbiddenError(
                f"Knowledge base '{kn_code}' not found or access not permitted. "
                f"Authorized KB codes: {authorized_codes}"
            )
            error("[dispatcher] %s failed: %s", operation_type.value, exc)
            return _format_operation_error(
                operation_type=operation_type,
                service_name="",
                path="",
                exc=exc,
            )

        path = kb.operations.get(operation_type)
        if not path:
            supported = [op.value for op in kb.operations]
            exc = OperationNotSupportedError(
                f"KB '{kn_code}' does not support '{operation_type.value}'. "
                f"Supported operations: {supported}"
            )
            error("[dispatcher] %s failed: %s", operation_type.value, exc)
            return _format_operation_error(
                operation_type=operation_type,
                service_name=kb.service_name,
                path="",
                exc=exc,
            )

        headers = _normalize_headers(kb.headers)

        try:
            if kb.base_url:
                info(
                    "[dispatcher] %s: direct mode url=%s%s",
                    operation_type.value,
                    kb.base_url.rstrip("/"),
                    "/" + path.lstrip("/"),
                )
                resp = await _post_direct_json(
                    base_url=kb.base_url,
                    path=path,
                    json_body=payload,
                    headers=headers,
                )
            else:
                info(
                    "[dispatcher] %s: discovery mode service=%s path=%s",
                    operation_type.value,
                    kb.service_name,
                    path,
                )
                kwargs: dict[str, Any] = {
                    "service_name": kb.service_name,
                    "path": path,
                    "json": payload,
                }
                if headers:
                    kwargs["headers"] = headers
                resp = await post_discovered_json(**kwargs)
        except Exception as exc:
            error(
                "[dispatcher] %s failed: service=%s base_url=%s path=%s error=%s",
                operation_type.value,
                kb.service_name,
                kb.base_url,
                path,
                exc,
            )
            return _format_operation_error(
                operation_type=operation_type,
                service_name=kb.service_name,
                path=path,
                exc=exc,
            )
        if resp.get("resultCode") != "0":
            result_msg = resp.get("resultMsg", "unknown error")
            error(
                "[dispatcher] %s API error: service=%s base_url=%s path=%s resultMsg=%s",
                operation_type.value,
                kb.service_name,
                kb.base_url,
                path,
                result_msg,
            )
        return resp


class DispatcherToolMiddleware(AgentMiddleware):
    """Post-processes dispatcher tool results: injects index_id, artifact, follow-up prompt."""

    def __init__(
        self,
        index_id_fn: Callable[[int, int, int], str],
        follow_up_prompt: str,
    ) -> None:
        self._index_id_fn = index_id_fn
        self._follow_up_prompt = follow_up_prompt
        self._search_tool_name = OPERATION_REGISTRY[
            OperationType.KNOWLEDGE_SEARCH
        ].tool_name
        self._metadata_fields_tool_name = OPERATION_REGISTRY[
            OperationType.METADATA_FIELDS_LIST
        ].tool_name
        self._dsl_guide_tool_name = OPERATION_REGISTRY[
            OperationType.DSL_GUIDE
        ].tool_name
        self._counter_lock = asyncio.Lock()
        self._result_counters: dict[tuple[str, int, int], int] = {}

    @staticmethod
    def _parse_where(raw: Any) -> Any:
        """Parse a JSON-string 'where' argument into a dict, if needed."""
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                pass
        return raw

    @staticmethod
    def _metadata_fields_available(runtime_context: Any) -> bool:
        """Check whether any KB supports METADATA_FIELDS_LIST."""
        if runtime_context is None:
            raise RuntimeError(
                "DispatcherToolMiddleware requires a non-null runtime context"
            )
        kbs = runtime_context.retrieval.knowledge_bases
        return any(
            OperationType.METADATA_FIELDS_LIST in getattr(kb, "operations", {})
            for kb in kbs
        )

    def _check_dsl_prerequisites(
        self, state: dict, runtime_context: Any = None
    ) -> list[str]:
        messages = state.get("messages", [])
        found_metadata_fields = False
        found_dsl_guide = False
        for msg in messages:
            if isinstance(msg, ToolMessage):
                if msg.name == self._metadata_fields_tool_name:
                    found_metadata_fields = True
                elif msg.name == self._dsl_guide_tool_name:
                    found_dsl_guide = True
        missing = []
        if not found_metadata_fields:
            missing.append(self._metadata_fields_tool_name)
        if not found_dsl_guide:
            missing.append(self._dsl_guide_tool_name)
        if not self._metadata_fields_available(runtime_context):
            missing = [
                t
                for t in missing
                if t not in {self._metadata_fields_tool_name, self._dsl_guide_tool_name}
            ]
        return missing

    async def abefore_model(
        self,
        state: StateT,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        """Inject follow-up prompt if the last tool call was a knowledge search."""
        _ = runtime
        messages = state.get("messages", [])
        if not messages:
            return None
        last_msg = messages[-1]
        if (
            isinstance(last_msg, ToolMessage)
            and last_msg.name == self._search_tool_name
        ):
            return {
                "messages": [
                    HumanMessage(
                        content=self._follow_up_prompt,
                        additional_kwargs=agent_metadata("dispatcher"),
                    )
                ]
            }
        return None

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable
    ) -> ToolMessage | Command:
        tool_args = request.tool_call.get("args", {})
        where = self._parse_where(tool_args.get("where"))
        if where is not None and where != {}:
            runtime_context = request.runtime.context
            if not self._metadata_fields_available(runtime_context):
                return ToolMessage(
                    content=json.dumps(
                        {
                            "error": True,
                            "error_type": "WhereNotSupported",
                            "message": (
                                "The 'where' parameter is not supported because no "
                                "knowledge base provides metadata field listing. "
                                "Remove the 'where' parameter and retry your search."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    name=request.tool_call["name"],
                    tool_call_id=request.tool_call["id"],
                )
            missing = self._check_dsl_prerequisites(request.state, runtime_context)
            if missing:
                return ToolMessage(
                    content=json.dumps(
                        {
                            "error": True,
                            "error_type": "DslPrerequisiteNotMet",
                            "message": (
                                "Before using 'where', you must call "
                                f"{', '.join(missing)} first to understand "
                                "available fields and DSL syntax."
                            ),
                            "missing_tools": missing,
                        },
                        ensure_ascii=False,
                    ),
                    name=request.tool_call["name"],
                    tool_call_id=request.tool_call["id"],
                )

        result = await handler(request)
        if request.tool_call["name"] != self._search_tool_name:
            return result
        return await self._post_process_search(result, request)

    async def _reserve_item_ids(
        self,
        *,
        run_scope_id: str,
        sub_query_idx: int,
        step: int,
        count: int,
    ) -> range:
        counter_key = (run_scope_id, sub_query_idx, step)
        async with self._counter_lock:
            start = self._result_counters.get(counter_key, 0)
            self._result_counters[counter_key] = start + count
        return range(start + 1, start + count + 1)

    async def _post_process_search(
        self, result: Any, request: ToolCallRequest
    ) -> Command:
        state = request.state
        step = state.get("current_step", 0)
        sub_query_idx = int(state.get("sub_query_idx", 0))
        execution_info = getattr(request.runtime, "execution_info", None)
        run_scope_id = (
            request.runtime.config.get("metadata", {}).get("message_id")
            or getattr(execution_info, "run_id", None)
            or request.runtime.config.get("run_id")
            or request.runtime.config.get("metadata", {}).get("session_id")
            or "default"
        )
        try:
            raw_results: list[dict[str, Any]] = json.loads(result.content)
        except Exception:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=result.content,
                            artifact=None,
                            name=request.tool_call["name"],
                            tool_call_id=request.tool_call["id"],
                        )
                    ]
                }
            )

        item_ids = await self._reserve_item_ids(
            run_scope_id=run_scope_id,
            sub_query_idx=sub_query_idx,
            step=step,
            count=len(raw_results),
        )
        indexed = [
            {**item, "index_id": self._index_id_fn(sub_query_idx, step, item_id)}
            for item, item_id in zip(raw_results, item_ids, strict=False)
        ]
        llm_results = [
            {"index_id": item["index_id"], "content": item["content"]}
            for item in indexed
        ]

        return Command(
            update={
                "retrieval_results": indexed,
                "messages": [
                    ToolMessage(
                        content=json.dumps(llm_results, ensure_ascii=False),
                        artifact=indexed,
                        name=request.tool_call["name"],
                        id=getattr(result, "id", None),
                        tool_call_id=request.tool_call["id"],
                    ),
                ],
            }
        )


__all__ = [
    "DispatcherToolMiddleware",
    "ServiceToolDispatcher",
]
