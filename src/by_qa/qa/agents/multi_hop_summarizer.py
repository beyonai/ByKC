"""Multi-hop summary agent using LangGraph create_agent."""

from enum import Enum
from typing import Annotated, Any, Dict, List, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.core.logger import info
from by_qa.core.model_config import LLMModelProfile
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.fallback_messages import FallbackMessage
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.common.state import SubAnswer
from by_qa.qa.services.llm_service import LLMService

DEFAULT_MULTI_HOP_SUMMARY_PROMPT = (
    """# Role

You are a professional multi-hop reasoning summarization expert. Your task is to receive the original question and the hop-by-hop reasoning results from the multi-hop retrieval agent, and synthesize them into a well-structured, evidence-backed final report.

Your core principle: **Stay faithful to the retrieved evidence, present the complete reasoning chain, and never add information not present in the retrieval results.**

---

# Input Description

You will receive the following:
- **Original question**: The complete question posed by the user
- **Multi-hop reasoning results**: Including each hop's sub-question, retrieved evidence, and that step's conclusion

---

# Summarization Methodology

## Step 1: Review Reasoning Chain Completeness

Before generating the report, evaluate the reasoning results received:

- Is the reasoning chain complete (does each step connect from the original question to the final answer)?
- Is each hop's conclusion supported by evidence?
- Are there any parts with insufficient evidence or broken reasoning links?

## Step 2: Synthesize and Generate Report

Based on the review, generate the final report following the output format below.

---

# Answer Generation Standards

## Rigor Requirements

- All factual statements must be traceable to retrieved evidence
- Clearly distinguish two types of content:
  - **Facts directly supported by evidence**: Information explicitly contained in retrieval results
  - **Reasonable inferences based on evidence**: Must be marked with phrases like "inferred based on available information"
- When evidence from different hops is contradictory, present the different accounts honestly without arbitrarily choosing sides
- Fabricating information not present in retrieval results is prohibited
- Skipping reasoning steps to jump directly to conclusions is prohibited

## Output Format

### Conclusion

Present the final answer first, answering the original question concisely and clearly.

### Reasoning Path

Show the complete reasoning process hop by hop, with each hop including:
- **Sub-question**: The question this hop was answering
- **Key evidence**: The core evidence supporting this step's conclusion, summarized in your own words (do not copy in full, and do not attach identifiers)
- **Step conclusion**: The answer derived from the evidence

Show the logical connections between hops.

Do not append a "Sources" section or any list of evidence identifiers — see "Citation Marker Prohibition" below.

## Citation Marker Prohibition

**The frontend does not render citations, so the final report must NOT contain any citation markers or reference identifiers.**

Specifically forbidden patterns include, but are not limited to:
- Bracketed identifiers such as `[xx-yy-zz]`, `[1]`, `[doc-123]`, `[ref-1]`
- Full-width bracketed identifiers such as `【xx-yy-zz】`, `【1】`, `【来源1】`
- Footnote-style markers (`^1`, `[^1]`), parenthesized IDs, or any inline reference tags
- A trailing "Sources" / "References" / "参考资料" section listing evidence IDs

The evidence-driven principle remains unchanged: every factual statement must still be traceable to retrieved evidence **internally**. But the final report must read as clean natural prose, with the supporting facts paraphrased into the sentences themselves rather than tagged with identifiers.

If you need to attribute information to a source, do so by naming the source in prose (e.g., "according to the company's 2024 financial filing") rather than by inserting an identifier.

## Handling Insufficient Evidence

Based on the completeness of the reasoning chain, adopt different output strategies:

| Reasoning Chain Status | Output Strategy |
|---------|---------|
| Evidence sufficient for all hops and reasoning chain complete | Output complete answer and reasoning path normally, in clean prose with no citation markers |
| Evidence sufficient for some hops, insufficient for others | Output the reasoning path supported by existing evidence in clean prose, clearly indicating which parts have insufficient evidence or uncertainty (still no citation markers) |
| Critical parts severely lack evidence, reasoning chain broken | Honestly state that a complete conclusion cannot be reached, show the partial reasoning completed and limited information collected (still no citation markers) |

"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


class MultiHopSummaryNodeNames(str, Enum):
    ENTRY = "mh_summary_entry"
    AGENT = "mh_summary_agent"
    SUMMARY = "mh_summary_summary"


class MultiHopSummaryAgentState(TypedDict):
    """State for the multi-hop summary subgraph."""

    messages: Annotated[list, add_messages]
    sub_query: dict
    intermediate_results: list
    retrieval_results: Annotated[list, merge_list_with_mode]
    sub_answers: Annotated[list, merge_list_with_mode]


def _extract_sources(retrieval_results: List[Dict]) -> List[Dict]:
    sources = []
    seen = set()
    for result in retrieval_results:
        key = result.get("source", "") + result.get("content", "")[:50]
        if key not in seen:
            seen.add(key)
            sources.append(
                {
                    "content": result.get("content", ""),
                    "source": result.get("source", ""),
                    "source_type": result.get("source_type", ""),
                    "score": result.get("score", 0.0),
                    "step": result.get("step"),
                }
            )
    return sources


def _calculate_confidence(retrieval_results: List[Dict]) -> float:
    if not retrieval_results:
        return 0.0
    scores = [r.get("score", 0.0) for r in retrieval_results[:3]]
    return sum(scores) / len(scores) if scores else 0.0


def _build_intermediate_context(
    intermediate_results: List[Dict], retrieval_results: List[Dict]
) -> str:
    retrieval_by_index = {}
    for result in retrieval_results:
        index_id = result.get("index_id")
        if index_id:
            retrieval_by_index[index_id] = result

    context_parts = []
    for i, result in enumerate(intermediate_results, 1):
        answer = result.get("answer", "")
        query = result.get("query", "")
        source_indices = result.get("source_indices", [])
        source_contents = []
        for idx in source_indices:
            retrieval = retrieval_by_index.get(idx)
            if retrieval:
                content = retrieval.get("content", "")
                source_type = retrieval.get("source_type", "unknown")
                source = retrieval.get("source", "unknown")
                source_contents.append(f"[({source_type}) {source}\n{content}")
        if answer or source_contents:
            step_context = f"Step {i}:\n"
            step_context += f"Sub-query: {query}\n"
            if source_contents:
                step_context += (
                    "Referenced sources:\n"
                    + "\n".join(f"  - {s}" for s in source_contents)
                    + "\n"
                )
            if answer:
                step_context += f"Answer: {answer}\n"
            context_parts.append(step_context)

    return (
        "\n".join(context_parts)
        if context_parts
        else FallbackMessage.NO_INTERMEDIATE_STEPS
    )


async def mh_summary_entry_node(
    state: MultiHopSummaryAgentState,
) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    if state.get("sub_answers"):
        info("[multi_hop] Summary entry: sub_answers already exist, skipping")
        return {"sub_answers": state["sub_answers"]}

    intermediate_results = state.get("intermediate_results", [])
    retrieval_results = state.get("retrieval_results", [])
    intermediate_context = _build_intermediate_context(
        intermediate_results, retrieval_results
    )
    info("[multi_hop] Summary entry: building context for LLM")
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=(
                    f"Original question: {sub_query.get('query_text', '')}\n\n"
                    "Multi-hop retrieval step details (including queries, answers, and referenced source content for each step):\n"
                    f"{intermediate_context}\n\n"
                    "Please integrate the above information and generate a final comprehensive answer."
                ),
                additional_kwargs=agent_metadata(MultiHopSummaryNodeNames.ENTRY.value),
            ),
        ],
    }


async def mh_summary_summary_node(
    state: MultiHopSummaryAgentState,
) -> Dict[str, Any]:
    if state.get("sub_answers"):
        return {}

    sub_query = state.get("sub_query", {})
    intermediate_results = state.get("intermediate_results", [])
    retrieval_results = state.get("retrieval_results", [])

    final_answer = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage) and getattr(msg, "content", ""):
            final_answer = msg.content
            break

    sub_answer = SubAnswer(
        sub_query_id=sub_query.get("query_id", "unknown"),
        sub_query_text=sub_query.get("query_text", ""),
        query_type="multi-hop",
        answer=final_answer,
        reasoning_chain=[r.get("answer", "") for r in intermediate_results],
        intermediate_answers=intermediate_results,
        sources=_extract_sources(retrieval_results),
        confidence=_calculate_confidence(retrieval_results),
        retrieval_results=retrieval_results,
    )
    info(
        "[multi_hop] Summary node generated final answer: "
        f"query={sub_query.get('query_text', '')}, final_answer={final_answer}"
    )
    return {"sub_answers": [sub_answer], "messages": [AIMessage(content=final_answer)]}


def _route_after_entry(state: Dict[str, Any]) -> str:
    if state.get("sub_answers"):
        return MultiHopSummaryNodeNames.SUMMARY.value
    return MultiHopSummaryNodeNames.AGENT.value


async def build_multi_hop_summary_subgraph(
    *,
    llm_service: LLMService,
    override: AgentOverride | None = None,
    checkpointer=None,
):
    override = override or AgentOverride()
    llm = await llm_service._get_streaming_model(LLMModelProfile.STANDARD)
    agent_graph = create_agent(
        model=llm,
        tools=[],
        middleware=list(override.middleware),
        state_schema=MultiHopSummaryAgentState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=override.prompt or DEFAULT_MULTI_HOP_SUMMARY_PROMPT,
    )
    workflow = StateGraph(MultiHopSummaryAgentState, context_schema=QARuntimeContext)
    workflow.add_node(MultiHopSummaryNodeNames.ENTRY.value, mh_summary_entry_node)
    workflow.add_node(MultiHopSummaryNodeNames.AGENT.value, agent_graph)
    workflow.add_node(MultiHopSummaryNodeNames.SUMMARY.value, mh_summary_summary_node)
    workflow.set_entry_point(MultiHopSummaryNodeNames.ENTRY.value)
    workflow.add_conditional_edges(
        MultiHopSummaryNodeNames.ENTRY.value,
        _route_after_entry,
        {
            MultiHopSummaryNodeNames.AGENT.value: MultiHopSummaryNodeNames.AGENT.value,
            MultiHopSummaryNodeNames.SUMMARY.value: MultiHopSummaryNodeNames.SUMMARY.value,
        },
    )
    workflow.add_edge(
        MultiHopSummaryNodeNames.AGENT.value, MultiHopSummaryNodeNames.SUMMARY.value
    )
    workflow.add_edge(MultiHopSummaryNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "DEFAULT_MULTI_HOP_SUMMARY_PROMPT",
    "MultiHopSummaryAgentState",
    "MultiHopSummaryNodeNames",
    "build_multi_hop_summary_subgraph",
    "mh_summary_entry_node",
    "mh_summary_summary_node",
]
