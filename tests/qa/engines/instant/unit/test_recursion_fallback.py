"""Tests for preemptive recursion-limit fallback synthesis."""

import json
import operator
from types import SimpleNamespace
from typing import Annotated, Any, TypedDict

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from by_qa.core.model_config import LLMModelProfile
from by_qa.qa.agents.multi_hop_react import build_multi_hop_subgraph
from by_qa.qa.agents.recursion_fallback import _deterministic_fallback_answer
from by_qa.qa.agents.single_hop_react import build_single_hop_subgraph
from by_qa.qa.common.config import AgentOverride, QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware


class _ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCapableFakeModel":
        del tools, kwargs
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-recursion-fallback"


class _FakeLLMService:
    def __init__(self, models: list[_ToolCapableFakeModel]) -> None:
        self.models = list(models)

    async def _get_streaming_model(self, model_type) -> _ToolCapableFakeModel:
        assert model_type == LLMModelProfile.STANDARD
        return self.models.pop(0)

    async def get_model_config(self, model_type):
        assert model_type == LLMModelProfile.STANDARD
        return SimpleNamespace(max_model_len=None)


@pytest.mark.asyncio
async def test_dispatcher_routes_to_fallback_when_two_graph_steps_remain():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx, step, item_id: (
            f"{sub_query_idx}-{step}-{item_id}"
        ),
        follow_up_prompt="continue",
    )

    assert await middleware.abefore_model({"remaining_steps": 2}, None) == {
        "recursion_fallback_required": True,
        "jump_to": "end",
    }


@pytest.mark.asyncio
async def test_single_hop_loop_limit_generates_answer_from_partial_retrievals():
    @tool
    async def search_knowledge(query: str) -> str:
        """Return one fake evidence item."""
        return json.dumps(
            [
                {
                    "content": f"evidence for {query}",
                    "source": "/policy.md",
                    "source_type": "knowledge_base",
                    "score": 0.9,
                }
            ]
        )

    looping_model = _ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="partial agent reply" if index == 0 else "",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": f"query-{index}"},
                        "id": f"tc-{index}",
                        "type": "tool_call",
                    }
                ],
            )
            for index in range(20)
        ]
    )
    fallback_model = _ToolCapableFakeModel(
        responses=[AIMessage(content="best partial answer")]
    )
    llm_service = _FakeLLMService([looping_model, fallback_model])

    graph = await build_single_hop_subgraph(
        agent_override=AgentOverride(tools=[search_knowledge]),
        llm_service=llm_service,
        checkpointer=False,
    )

    result = await graph.ainvoke(
        {
            "sub_query": {"query_id": "sq_1", "query_text": "question"},
            "sub_query_idx": 0,
            "sub_answers": [],
            "retrieval_results": [],
            "messages": [HumanMessage(content="go")],
            "cited_indices": [],
            "result_counter": 0,
        },
        config={"recursion_limit": 50},
        context=QARuntimeContext(
            retrieval=QARetrievalConfig(),
            llm_service=llm_service,
        ),
    )

    assert result["recursion_fallback_required"] is True
    assert len(result["retrieval_results"]) > 8
    assert result["retrieval_results"][0]["content"] == "evidence for query-0"
    assert result["sub_answers"][0]["answer"] == "best partial answer"
    assert result["sub_answers"][0]["retrieval_results"] == result["retrieval_results"]


@pytest.mark.asyncio
async def test_multi_hop_loop_limit_skips_normal_summary_and_uses_fallback():
    @tool
    async def search_knowledge(query: str) -> str:
        """Return one fake evidence item."""
        return json.dumps(
            [
                {
                    "content": f"multi-hop evidence for {query}",
                    "source": "/multi-hop.md",
                    "source_type": "knowledge_base",
                    "score": 0.8,
                }
            ]
        )

    looping_model = _ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": "first hop"},
                        "id": "tc-multi",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    unused_summary_model = _ToolCapableFakeModel(
        responses=[AIMessage(content="must not be returned")]
    )
    fallback_model = _ToolCapableFakeModel(
        responses=[AIMessage(content="multi-hop partial answer")]
    )
    llm_service = _FakeLLMService([looping_model, unused_summary_model, fallback_model])

    graph = await build_multi_hop_subgraph(
        agent_override=AgentOverride(tools=[search_knowledge]),
        llm_service=llm_service,
        checkpointer=False,
    )

    result = await graph.ainvoke(
        {
            "sub_query": {
                "query_id": "sq_1",
                "query_text": "multi-hop question",
                "reasoning_chain": ["first hop", "second hop"],
            },
            "sub_query_idx": 0,
            "sub_answers": [],
            "retrieval_results": [],
            "messages": [HumanMessage(content="go")],
            "intermediate_results": [],
            "reasoning_chain": [],
            "result_counter": 0,
        },
        config={"recursion_limit": 6},
        context=QARuntimeContext(
            retrieval=QARetrievalConfig(),
            llm_service=llm_service,
        ),
    )

    assert result["recursion_fallback_required"] is True
    assert result["sub_answers"][0]["answer"] == "multi-hop partial answer"
    assert result["sub_answers"][0]["query_type"] == "multi-hop"
    assert result["retrieval_results"][0]["content"] == (
        "multi-hop evidence for first hop"
    )


def test_deterministic_fallback_prefers_partial_agent_reply():
    answer = _deterministic_fallback_answer(
        "问题",
        [{"content": "检索证据"}],
        ["已有的阶段性回答"],
    )

    assert answer == "已有的阶段性回答"


class _ParallelParentState(TypedDict):
    queries: list[str]
    sub_answers: Annotated[list[dict[str, Any]], operator.add]
    final_answer: str


def _dispatch_parallel_single_hop_workers(state: _ParallelParentState):
    return [
        Send(
            "worker",
            {
                "sub_query": {
                    "query_id": f"sq-{index}",
                    "query_text": query,
                },
                "sub_query_idx": index,
                "sub_answers": [],
                "retrieval_results": [],
                "messages": [],
                "cited_indices": [],
                "result_counter": 0,
            },
        )
        for index, query in enumerate(state["queries"])
    ]


async def _aggregate_parallel_answers(state: _ParallelParentState):
    return {
        "final_answer": " | ".join(
            sorted(answer["answer"] for answer in state["sub_answers"])
        )
    }


@pytest.mark.asyncio
async def test_parallel_workers_share_recursion_steps_and_all_reach_fallback():
    @tool
    async def search_knowledge(query: str) -> str:
        """Return one evidence item while preserving parallel scheduling."""
        return json.dumps(
            [
                {
                    "content": f"parallel evidence for {query}",
                    "source": "/parallel.md",
                    "source_type": "knowledge_base",
                    "score": 0.9,
                }
            ]
        )

    looping_model = _ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": f"parallel-{index}"},
                        "id": f"tc-parallel-{index}",
                        "type": "tool_call",
                    }
                ],
            )
            for index in range(30)
        ]
    )
    fallback_models = [
        _ToolCapableFakeModel(responses=[AIMessage(content=f"fallback-{index}")])
        for index in range(3)
    ]
    llm_service = _FakeLLMService([looping_model, *fallback_models])
    worker = await build_single_hop_subgraph(
        agent_override=AgentOverride(tools=[search_knowledge]),
        llm_service=llm_service,
        checkpointer=False,
    )

    builder = StateGraph(_ParallelParentState)
    builder.add_conditional_edges(
        START, _dispatch_parallel_single_hop_workers, ["worker"]
    )
    builder.add_node("worker", worker)
    builder.add_node("aggregate", _aggregate_parallel_answers)
    builder.add_edge("worker", "aggregate")
    builder.add_edge("aggregate", END)
    graph = builder.compile()

    result = await graph.ainvoke(
        {
            "queries": ["question-a", "question-b", "question-c"],
            "sub_answers": [],
            "final_answer": "",
        },
        config={"recursion_limit": 14},
        context=QARuntimeContext(
            retrieval=QARetrievalConfig(),
            llm_service=llm_service,
        ),
    )

    answers = result["sub_answers"]
    assert len(answers) == 3
    assert {answer["sub_query_id"] for answer in answers} == {
        "sq-0",
        "sq-1",
        "sq-2",
    }
    assert {answer["answer"] for answer in answers} == {
        "fallback-0",
        "fallback-1",
        "fallback-2",
    }
    assert {len(answer["retrieval_results"]) for answer in answers} == {4}
    assert result["final_answer"] == "fallback-0 | fallback-1 | fallback-2"
