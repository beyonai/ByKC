"""Integration tests for parallel search tool calls in single-hop and multi-hop flows."""

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from by_qa.qa.instant.config import InstantSearchRetrievalConfig
from by_qa.qa.instant.graphs.multi_hop import build_multi_hop_subgraph
from by_qa.qa.instant.graphs.single_hop import build_single_hop_subgraph
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.operation_registry import SearchInput


class _ToolCapableFakeModel(FakeMessagesListChatModel):
    """Fake model with tool binding support for create_agent."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCapableFakeModel":
        del tools, kwargs
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-parallel-tool-capable"


class _FakeLLMService:
    """Minimal llm service adapter for graph builders."""

    def __init__(self, retrieval_model: _ToolCapableFakeModel) -> None:
        self._retrieval_model = retrieval_model

    async def _get_streaming_model(self, model_type: str) -> _ToolCapableFakeModel:
        del model_type
        return self._retrieval_model

    async def generate(
        self, messages: list[Any], model_type: str, json_mode: bool
    ) -> str:
        del messages, model_type, json_mode
        return "summary"


class _ParallelSearchProbe:
    """Async search tool that records the maximum observed concurrency."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    def build_tool(self):
        probe = self

        @tool("search_knowledge", args_schema=SearchInput)
        async def search_knowledge(
            query: str, kn_code_list: list[str] | None = None
        ) -> str:
            """Fake parallel-safe search tool."""
            del kn_code_list
            probe.active += 1
            probe.max_active = max(probe.max_active, probe.active)
            try:
                await asyncio.sleep(0.01)
                return json.dumps(
                    [
                        {
                            "content": f"hit:{query}",
                            "source": f"/{query}.md",
                            "source_type": "knowledge_base",
                            "score": 0.9,
                        }
                    ],
                    ensure_ascii=False,
                )
            finally:
                probe.active -= 1

        return search_knowledge


def _single_hop_model() -> _ToolCapableFakeModel:
    return _ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": "alpha"},
                        "id": "tc-alpha",
                        "type": "tool_call",
                    },
                    {
                        "name": "search_knowledge",
                        "args": {"query": "beta"},
                        "id": "tc-beta",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="done"),
        ]
    )


def _multi_hop_model() -> _ToolCapableFakeModel:
    return _ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": "alpha"},
                        "id": "tc-alpha",
                        "type": "tool_call",
                    },
                    {
                        "name": "search_knowledge",
                        "args": {"query": "beta"},
                        "id": "tc-beta",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="done"),
        ]
    )


@pytest.mark.asyncio
async def test_single_hop_subgraph_handles_parallel_search_calls_without_state_conflict():
    probe = _ParallelSearchProbe()
    llm_service = _FakeLLMService(_single_hop_model())

    with patch(
        "by_qa.qa.instant.graphs.single_hop.create_checkpointer_async",
        return_value=None,
    ):
        graph = await build_single_hop_subgraph(
            config={"tools": [probe.build_tool()]},
            llm_service=llm_service,
        )

    result = await graph.ainvoke(
        {
            "sub_query": {"query_id": "sq_1", "query_text": "single hop"},
            "sub_query_idx": 0,
            "sub_answers": [],
            "retrieval_results": [],
            "messages": [HumanMessage(content="go")],
            "cited_indices": [],
            "result_counter": 0,
        }
    )

    assert probe.max_active >= 2
    assert len(result["retrieval_results"]) == 2
    assert result["sub_answers"][0]["answer"] == "done"


@pytest.mark.asyncio
async def test_multi_hop_subgraph_handles_parallel_search_calls_without_state_conflict():
    probe = _ParallelSearchProbe()
    llm_service = _FakeLLMService(_multi_hop_model())

    with patch(
        "by_qa.qa.instant.graphs.multi_hop.create_checkpointer_async",
        return_value=None,
    ):
        graph = await build_multi_hop_subgraph(
            config={"tools": [probe.build_tool()]},
            llm_service=llm_service,
        )

    result = await graph.ainvoke(
        {
            "sub_query": {
                "query_id": "sq_1",
                "query_text": "multi hop",
                "reasoning_chain": ["step 1", "step 2"],
            },
            "sub_query_idx": 0,
            "messages": [HumanMessage(content="go")],
            "reasoning_plan": [],
            "current_step": 0,
            "intermediate_results": [],
            "current_hop": 0,
            "intermediate_answers": [],
            "reasoning_chain": [],
            "all_retrieval_results": [],
            "sub_answers": [],
            "result_counter": 0,
        },
        context=InstantSearchRuntimeContext(
            retrieval=InstantSearchRetrievalConfig(),
            llm_service=llm_service,
        ),
    )

    assert probe.max_active >= 2
    assert result["sub_answers"][0]["answer"] == "summary"
