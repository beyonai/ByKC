"""Single-hop agent assembly for the instant-search capability."""

from typing import Any, List

from langchain.agents import create_agent

from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.instant.runtime.tool_call_guard import ToolCallGuardMiddleware
from by_qa.qa.instant.state import SingleHopState
from by_qa.qa.services.llm_service import LLMService
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware

DEFAULT_SINGLE_HOP_SYSTEM_PROMPT = (
    """You are an intelligent single-hop retrieval QA assistant.

Your task is to answer a single-hop question. "Single-hop" means no multi-step dependency reasoning is needed, but it does not mean a single retrieval is always sufficient.

[Workflow]
1. First analyze what information is still missing for the current question
2. Call search_knowledge to collect evidence, can be called multiple times
3. When you believe the evidence is sufficient, output the final answer directly based on existing evidence

[Tool Description]
- search_knowledge: Perform retrieval, returns evidence summaries with index_id

[Important Rules]
- If evidence is insufficient, continue retrieving, do not guess
- When generating the final answer, explicitly cite the evidence IDs you used
- The final answer should be direct, complete, and faithful to the retrieved evidence
"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


async def build_single_hop_agent_graph(
    *,
    system_prompt: str | None = None,
    extra_tools: List[Any] | None = None,
    extra_middleware: List[Any] | None = None,
    llm_service: LLMService,
    checkpointer: Any | None = None,
):
    """Build the configurable single-hop agent graph."""
    llm = await llm_service._get_streaming_model("retrieval")
    tools = list(extra_tools or [])
    middleware = [
        ToolCallGuardMiddleware(),
        DispatcherToolMiddleware(
            index_id_fn=lambda sub_query_idx, step, item_id: (
                f"{sub_query_idx}-{step}-{item_id}"
            ),
            follow_up_prompt="If the current evidence is still insufficient to answer the question, continue calling search_knowledge to collect more information; if it is already sufficient, output the final answer directly based on existing evidence, do not call tools again.",
        ),
    ] + list(extra_middleware or [])
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        state_schema=SingleHopState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or DEFAULT_SINGLE_HOP_SYSTEM_PROMPT,
    )


__all__ = [
    "DEFAULT_SINGLE_HOP_SYSTEM_PROMPT",
    "build_single_hop_agent_graph",
]
