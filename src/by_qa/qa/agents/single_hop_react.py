"""Self-contained single-hop ReAct agent: state, nodes, and subgraph builder."""

from enum import Enum
from typing import Annotated, Any, Dict, List, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from by_qa.core.logger import info
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.middleware.tool_call_guard import ToolCallGuardMiddleware
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.common.state import SubAnswer
from by_qa.qa.services.llm_service import LLMService
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class SingleHopState(TypedDict):
    """State for single-hop subgraph."""

    sub_query: dict[str, Any]
    sub_query_idx: int
    cited_indices: list[str]
    result_counter: int
    retrieval_results: Annotated[list[dict[str, Any]], merge_list_with_mode]
    sub_answers: Annotated[list[SubAnswer], merge_list_with_mode]
    messages: Annotated[list, add_messages]


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class SingleHopNodeNames(str, Enum):
    ENTRY = "single_hop_entry"
    AGENT = "single_hop_agent"
    SUMMARY = "single_hop_summary"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

DEFAULT_SINGLE_HOP_SYSTEM_PROMPT = (
    """# Role

You are a rigorous knowledge retrieval Q&A assistant, specialized in handling single-hop questions.

"Single-hop" means the question itself does not involve multi-step dependent reasoning — the answer points to a clear fact or conclusion. However, this does not mean a single retrieval is enough; you may need multiple rounds of retrieval to collect sufficient evidence.

Your core principle: **All conclusions must be evidence-driven; never speculate without basis.**

---

# Information Collection Methodology

## Step 1: Question Analysis

Before performing any retrieval, complete the following analysis:

- Identify the core entities in the question (names, concepts, events, dates, etc.)
- Clarify what specific information point needs to be answered
- Anticipate possible retrieval directions and keywords

## Step 2: Execute Retrieval

Construct queries based on the analysis results and execute retrieval. Follow these strategies:

**First round retrieval**: Use the core semantics of the question as the query, prioritizing coverage of the most directly relevant information.

**Result evaluation**: After each retrieval, immediately evaluate:
- Is the returned evidence directly relevant to the question?
- Does it already cover the key information points needed for the answer?
- Are there contradictions or content that needs cross-validation?

**Strategy adjustment**: If current results are unsatisfactory, adjust as follows:
- Retry with synonyms, near-synonyms, or keywords from different angles
- Narrow scope: Focus on a more specific sub-question
- Broaden scope: Use more general superordinate concepts
- Split queries: Break compound questions into multiple independent sub-queries and retrieve separately

## Step 3: Evidence Sufficiency Assessment

When deciding whether to continue retrieval, ask yourself:
- Can the existing evidence fully answer the question?
- Do all key information points have at least one piece of supporting evidence?
- If multiple pieces of evidence exist, are they consistent with each other?

Only proceed to the answer generation phase when evidence is sufficient and consistent.

---

# Termination Conditions

Use dynamic assessment based on "information gain" rather than fixed attempt limits:

**Normal termination**: Evidence is sufficient and can fully answer the question.

**Timely adjustment**: When a retrieval returns results that are irrelevant to the question or repeat existing information, you must immediately adjust retrieval strategy (change keywords, change angles) rather than repeatedly retrying with the same or similar queries.

**Gradual exit**: When you observe the following signals, you should stop retrieval and answer based on available information:
- Multiple consecutive rounds of retrieval have not brought new effective information
- Multiple different retrieval strategies have been attempted, but information gain is approaching zero
- Available retrieval angles have been essentially exhausted

After stopping retrieval, select the corresponding output strategy based on evidence sufficiency (see "Answer Generation Standards" below).

---

# Answer Generation Standards

## Rigor Requirements

- All factual statements must be supported by retrieved evidence
- Clearly distinguish two types of content:
  - **Facts directly supported by evidence**: Information explicitly contained in retrieval results
  - **Reasonable inferences based on evidence**: Must be marked with phrases like "inferred based on available information"
- When evidence is contradictory, present the different accounts honestly without arbitrarily choosing sides
- Fabricating information not present in retrieval results is prohibited

## Output Format

Adjust flexibly based on question complexity, but always maintain professional readability:

**Simple factual questions** (e.g., "What is X?", "Who is X?"):
- Provide the answer directly, with evidence source citations attached

**Questions requiring analysis or synthesis**:
- **Conclusion**: Present the core answer first
- **Analysis**: Elaborate on the key reasoning process, citing specific evidence
- **Sources**: List all cited evidence identifiers

## Citation Standards

- When citing evidence, **strictly use the identifiers actually returned in the retrieval results** (such as numbers, IDs, etc.), cited verbatim, without fabricating, renumbering, or using any identifiers not present in the retrieval results
- If retrieval results do not provide clear identifiers, cite by summarizing the source content of the evidence; do not generate numbers from thin air
- Summarize all cited evidence sources at the end of the answer
- Only cite evidence that was actually used; do not list retrieval results that were not referenced

## Handling Insufficient Evidence

Based on evidence sufficiency, adopt different output strategies:

| Evidence Status | Output Strategy |
|---------|---------|
| Sufficient and consistent | Output complete answer normally |
| Partially sufficient | Output the parts supported by existing evidence, clearly indicating which aspects have insufficient information or uncertainty |
| Severely insufficient | Honestly state that current retrieval was unable to find sufficient information to answer the question, briefly list the limited information collected for reference |
"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_final_answer(messages: List[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and getattr(message, "content", ""):
            return message.content
        if (
            isinstance(message, dict)
            and message.get("type") == "ai"
            and message.get("content")
        ):
            return message["content"]
    return ""


def _extract_sources(
    retrieval_results: List[Dict], cited_indices: List[str]
) -> List[Dict]:
    allowed = set(cited_indices or [])
    sources = []
    for result in retrieval_results:
        if allowed and result.get("index_id") not in allowed:
            continue
        sources.append(
            {
                "content": result.get("content", ""),
                "source": result.get("source", ""),
                "source_type": result.get("source_type", ""),
                "score": result.get("score", 0.0),
            }
        )
    return sources


def _calculate_confidence(
    retrieval_results: List[Dict], cited_indices: List[str]
) -> float:
    relevant_results = retrieval_results
    if cited_indices:
        cited_set = set(cited_indices)
        relevant_results = [
            r for r in retrieval_results if r.get("index_id") in cited_set
        ]
    if not relevant_results:
        return 0.0
    scores = [r.get("score", 0.0) for r in relevant_results[:3]]
    return sum(scores) / len(scores) if scores else 0.0


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


async def single_hop_entry_node(state: SingleHopState) -> Dict[str, Any]:
    """Initialize the single-hop agent state."""
    sub_query = state.get("sub_query", {})
    query_text = sub_query.get("query_text", "")
    info(f"[single_hop] Entry node for: {query_text[:50]}...")
    return {
        "messages": [
            HumanMessage(
                content=f"Answer this single-hop question: {query_text}",
                additional_kwargs=agent_metadata(SingleHopNodeNames.ENTRY.value),
            )
        ],
        "retrieval_results": {"mode": "RESET", "data": []},
        "cited_indices": [],
        "result_counter": 0,
    }


async def single_hop_summary_node(state: SingleHopState) -> Dict[str, Any]:
    """Build the single-hop sub-answer from the agent result state."""
    if state.get("sub_answers"):
        info("[single_hop] Summary node: sub_answers already exist, skipping")
        return {}

    sub_query = state.get("sub_query", {})
    query_id = sub_query.get("query_id", "unknown")
    query_text = sub_query.get("query_text", "")
    final_answer = _extract_final_answer(state.get("messages", []))
    retrieval_results = state.get("retrieval_results", [])
    cited_indices = state.get("cited_indices", [])

    sub_answer = SubAnswer(
        sub_query_id=query_id,
        sub_query_text=query_text,
        query_type="single-hop",
        answer=final_answer,
        reasoning_chain=[],
        intermediate_answers=[],
        sources=_extract_sources(retrieval_results, cited_indices),
        confidence=_calculate_confidence(retrieval_results, cited_indices),
        retrieval_results=retrieval_results,
    )
    info(
        "[single_hop] Summary node generated final answer: "
        f"query={query_text}, final_answer={final_answer}"
    )
    return {
        "sub_answers": [sub_answer],
        "messages": [AIMessage(content=final_answer)],
    }


# ---------------------------------------------------------------------------
# Agent & subgraph builders
# ---------------------------------------------------------------------------


async def build_single_hop_agent_graph(
    *,
    override: AgentOverride | None = None,
    llm_service: LLMService,
    checkpointer: Any | None = None,
):
    """Build the configurable single-hop agent graph."""
    override = override or AgentOverride()
    llm = await llm_service._get_streaming_model("retrieval")
    tools = list(override.tools)
    middleware = [
        ToolCallGuardMiddleware(),
        DispatcherToolMiddleware(
            index_id_fn=lambda sub_query_idx, step, item_id: (
                f"{sub_query_idx}-{step}-{item_id}"
            ),
            follow_up_prompt="If the current evidence is still insufficient to answer the question, continue calling search_knowledge to collect more information; if it is already sufficient, output the final answer directly based on existing evidence, do not call tools again.",
        ),
        *override.middleware,
    ]
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        state_schema=SingleHopState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=override.prompt or DEFAULT_SINGLE_HOP_SYSTEM_PROMPT,
    )


async def build_single_hop_subgraph(
    *,
    agent_override=None,
    llm_service=None,
    checkpointer=None,
):
    """Build single-hop subgraph using dedicated agent assembly."""
    if llm_service is None:
        raise ValueError("llm_service is required to build the single-hop subgraph")
    agent_graph = await build_single_hop_agent_graph(
        override=agent_override,
        llm_service=llm_service,
        checkpointer=checkpointer,
    )

    workflow = StateGraph(SingleHopState, context_schema=QARuntimeContext)
    workflow.add_node(SingleHopNodeNames.ENTRY.value, single_hop_entry_node)
    workflow.add_node(SingleHopNodeNames.AGENT.value, agent_graph)
    workflow.add_node(SingleHopNodeNames.SUMMARY.value, single_hop_summary_node)
    workflow.set_entry_point(SingleHopNodeNames.ENTRY.value)
    workflow.add_edge(SingleHopNodeNames.ENTRY.value, SingleHopNodeNames.AGENT.value)
    workflow.add_edge(SingleHopNodeNames.AGENT.value, SingleHopNodeNames.SUMMARY.value)
    workflow.add_edge(SingleHopNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "DEFAULT_SINGLE_HOP_SYSTEM_PROMPT",
    "SingleHopNodeNames",
    "SingleHopState",
    "build_single_hop_agent_graph",
    "build_single_hop_subgraph",
    "single_hop_entry_node",
    "single_hop_summary_node",
]
