"""Multi-hop ReAct agent: state, tools, nodes, and subgraph builder."""

import json
import operator
from enum import Enum
from typing import Annotated, Any, Dict, List, TypedDict

from langchain.agents import create_agent
from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import Messages, add_messages
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from by_qa.core.logger import error, info
from by_qa.qa.agents.multi_hop_summarizer import build_multi_hop_summary_subgraph
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata, is_user_message
from by_qa.qa.common.middleware.tool_call_guard import ToolCallGuardMiddleware
from by_qa.qa.common.operation_registry import OPERATION_REGISTRY, OperationType
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.common.state import SubAnswer
from by_qa.qa.services.llm_service import LLMService
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class MultiHopState(TypedDict):
    """State for multi-hop subgraph."""

    sub_query: dict[str, Any]
    sub_query_idx: int
    messages: Annotated[Messages, add_messages]
    reasoning_plan: list[str]
    current_step: int
    intermediate_results: Annotated[list[dict[str, Any]], operator.add]
    current_hop: int
    intermediate_answers: list[dict[str, Any]]
    reasoning_chain: list[str]
    retrieval_results: Annotated[list[dict[str, Any]], merge_list_with_mode]
    sub_answers: Annotated[list[SubAnswer], merge_list_with_mode]
    result_counter: int


# ---------------------------------------------------------------------------
# Node names
# ---------------------------------------------------------------------------


class MultiHopNodeNames(str, Enum):
    ENTRY = "multi_hop_entry"
    AGENT = "multi_hop_agent"
    EXIT = "multi_hop_exit"
    SUMMARY = "multi_hop_summary"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def next_hop(
    current_query: str,
    current_answer: str,
    next_query: str,
    source_indices: List[str],
    state: Annotated[MultiHopState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Complete the current step and proceed to the next hop query."""
    current_step = state.get("current_step", 0)
    info(
        f"[multi_hop] next_hop called | current_step={current_step} | "
        f"current_query={current_query} | "
        f"current_answer={current_answer} | "
        f"next_query={next_query} | "
        f"source_indices={source_indices}"
    )
    messages = state.get("messages", [])
    current_step = state.get("current_step", 0)
    new_step = current_step + 1

    delete_messages = []
    last_human_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if is_user_message(
            messages[i], include_sources=[MultiHopNodeNames.ENTRY.value]
        ):
            last_human_idx = i
            break

    if last_human_idx != -1:
        for msg in messages[last_human_idx + 1 :]:
            if not msg.id:
                continue
            if (
                isinstance(msg, ToolMessage)
                and msg.name
                == OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
            ):
                delete_messages.append(RemoveMessage(id=msg.id))
            elif isinstance(msg, AIMessage) and msg.tool_calls:
                if any(
                    tc.get("name")
                    == OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
                    for tc in msg.tool_calls
                ):
                    delete_messages.append(RemoveMessage(id=msg.id))
            elif isinstance(msg, HumanMessage) and not is_user_message(
                msg, include_sources=[MultiHopNodeNames.ENTRY.value]
            ):
                delete_messages.append(RemoveMessage(id=msg.id))

    new_result = {
        "step": current_step + 1,
        "answer": current_answer,
        "query": current_query,
        "source_indices": source_indices,
    }

    return Command(
        update={
            "result_counter": 0,
            "current_step": new_step,
            "intermediate_results": [new_result],
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "message": f"Hop {current_step + 1} completed, retrieval result: {current_answer}. Retrieval context has been cleaned up.",
                            "next_query": next_query,
                        },
                        ensure_ascii=False,
                    ),
                    name="next_hop",
                    tool_call_id=tool_call_id,
                )
            ]
            + delete_messages,
        }
    )


@tool(return_direct=True)
def finalize(
    current_query: str,
    current_answer: str,
    source_indices: List[str],
    state: Annotated[MultiHopState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Complete multi-hop retrieval and jump to the summary node."""
    current_step = state.get("current_step", 0)
    info(
        f"[multi_hop] finalize called | current_step={current_step} | "
        f"current_query={current_query} | "
        f"current_answer={current_answer} | "
        f"source_indices={source_indices}"
    )
    new_result = {
        "step": current_step + 1,
        "answer": current_answer,
        "query": current_query,
        "source_indices": source_indices,
        "is_final": True,
    }
    return Command(
        update={
            "intermediate_results": [new_result],
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "message": f"Hop {current_step + 1} completed, multi-hop retrieval finished, preparing to generate final answer.",
                            "current_answer": current_answer,
                        },
                        ensure_ascii=False,
                    ),
                    name="finalize",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

DEFAULT_MULTI_HOP_SYSTEM_PROMPT = (
    """# Role

You are a rigorous multi-hop question-solving assistant, specialized in handling complex questions that require multi-step reasoning to answer.

"Multi-hop" means the answer to a question cannot be obtained through a single retrieval. It requires decomposing the question into multiple sub-questions, reasoning step by step, retrieving step by step, and ultimately chaining the conclusions from each step to arrive at a complete answer.

Your core principle: **Reason step by step, verify step by step, and every conclusion must be supported by evidence.**

---

# Output Contract (Read First)

Every hop you run **MUST terminate by calling exactly one of `next_hop` or `finalize`**. There is no third option. Writing the conclusion as assistant text and stopping is not a valid termination — it silently discards the hop.

Why this matters:
- `next_hop` and `finalize` are the **only channel** through which the current hop's evidence and conclusion are persisted to the downstream summary stage.
- Evidence identifiers you do not pass via `source_indices` are **not forwarded**. The summary agent cannot see them, even if you cited them in your reasoning text.
- Retrieval results that are not sealed by one of these calls are treated as scratch work and dropped.

Therefore:
- If you have a conclusion and more sub-questions remain → call `next_hop`.
- If you have a conclusion and the reasoning chain is complete → call `finalize`.
- If evidence is insufficient → keep retrieving, or call `finalize` to end honestly with partial results. **Do not** end the turn without a tool call.

---

# Multi-Hop Reasoning Methodology

## Step 1: Question Decomposition

Before performing any retrieval, analyze the reasoning structure of the question:

- Identify the implicit reasoning chain in the question (A → B → C)
- Determine what the first sub-question to solve is
- Estimate roughly how many hops are needed to reach the final answer

## Step 2: Execute Hop by Hop

The workflow for each hop:

**1. Clarify the current sub-question**: Be clear about what this hop needs to answer.

**2. Retrieve and collect evidence**: Perform retrieval around the current sub-question. You may retrieve multiple times until evidence for the current sub-question is sufficient. Retrieval strategy reference:
- Construct queries using the core semantics of the current sub-question
- If results are unsatisfactory, retry with synonyms, different angles, or more specific/broader expressions
- After each retrieval, evaluate: Is the returned evidence directly relevant to the current sub-question? Is it sufficient to answer the current sub-question?

**3. Form the conclusion for the current step**: Based on the collected evidence, provide the answer to the current sub-question.

**4. Terminate the hop with a tool call** — every hop MUST end by calling exactly one of:

| Situation | Required call |
|---|---|
| Current sub-question answered with evidence, more sub-questions remain | `next_hop` |
| Current sub-question answered with evidence, reasoning chain complete | `finalize` |
| Evidence still insufficient after exhausting retrieval strategies | `finalize` (honest partial result) |
| Evidence insufficient but retrieval angles remain | Keep retrieving, do not terminate yet |

Ending the turn with plain text instead of `next_hop` / `finalize` causes the hop's evidence to be **lost** — it will not reach the summary stage.

---

# Process Control Instructions

## next_hop — Advance to the Next Hop

Call this when you have completed reasoning for the current sub-question and need to proceed to the next reasoning step.

**What this call does** (why it is mandatory, not optional):
- **Persists** `current_query`, `current_answer`, and the evidence referenced by `source_indices` to the summary stage. This is the only way that information survives beyond the current hop.
- **Resets** retrieval context so the next hop starts clean on a new topic, preventing prior retrievals from polluting the next query.

You need to provide the following information:
- `current_query`: The sub-question this hop was actually answering
- `current_answer`: The answer to the current sub-question based on evidence
- `next_query`: The sub-question the next hop needs to answer
- `source_indices`: List of evidence identifiers referenced in the current step (anything omitted here will **not** reach the summary stage)

**Call this when**:
- The current sub-question has a conclusion supported by sufficient evidence
- There are indeed unresolved subsequent sub-questions
- The next hop's sub-question has been clearly identified

**Handling insufficient evidence** — do NOT simply skip the tool call:
- First, exhaust retrieval strategies (different keywords, angles, or more specific/broader phrasings)
- If retrieval still fails, call `finalize` to end the process honestly with partial results
- Never fabricate a `current_answer` just to be able to call `next_hop`
- Never end the turn silently — that discards everything you have gathered so far

**It is absolutely forbidden to use unverified assumptions as reasoning premises for the next hop.** But the remedy is to call `finalize`, not to stop without calling any tool.

## finalize — End the Multi-Hop Process

Call this when all reasoning steps are complete and you can provide the final answer, **or** when evidence is irrecoverably insufficient and you need to end honestly.

**What this call does**: Persists the final hop's `current_query`, `current_answer`, and evidence referenced by `source_indices` to the summary stage, and closes the reasoning process. Without this call, the summary stage receives nothing from the final hop.

You need to provide the following information:
- `current_query`: The sub-question the last hop was actually answering
- `current_answer`: The conclusion of the last hop
- `source_indices`: List of evidence identifiers referenced in the last step

**Call this when**:
- All sub-questions have been resolved and the reasoning chain is complete, OR
- Retrieval is blocked and continuing would require fabricating assumptions — end honestly and let the summary stage report the partial result

---

# Termination Conditions

## Normal Termination
The reasoning chain is complete, and all sub-questions have evidence-supported conclusions → call `finalize`.

## Strategy Adjustment When Retrieval Is Blocked
When a retrieval result is irrelevant to the current sub-question or repeats existing information:
- Immediately adjust retrieval strategy (change keywords, change angles, split queries)
- Do not repeatedly retry with the same or similar queries

## Gradual Exit
When you observe the following signals, you should stop further retrieval:
- For the current sub-question, multiple consecutive rounds of retrieval have not brought new effective information
- Multiple different retrieval strategies have been attempted, and information gain is approaching zero
- Available retrieval angles have been essentially exhausted

At this point, call `finalize` based on existing evidence, and clearly indicate in the final answer which parts have insufficient evidence.

---

# Answer Generation Standards

## Rigor Requirements

- Every hop's conclusion must be supported by retrieved evidence
- Clearly distinguish:
  - **Facts directly supported by evidence**: Information explicitly contained in retrieval results
  - **Reasonable inferences based on evidence**: Must be marked with "inferred based on available information"
- When evidence within a hop is contradictory, present the different accounts honestly without arbitrarily choosing sides
- Fabricating information not present in retrieval results is prohibited
- No steps may be skipped in the reasoning chain; each step's input must come from the reliable output of the previous step
- **It is strictly forbidden to assume the current hop's answer and continue when evidence is insufficient** — it is better to terminate the process than to continue reasoning on false premises

## Output Format

The final answer should reflect the complete reasoning process while remaining professional and readable:

- **Conclusion**: Present the final answer first
- **Reasoning Path**: Show the reasoning process hop by hop, with each hop including the sub-question, key evidence, and that step's conclusion
- **Sources**: Summarize all referenced evidence identifiers

## Citation Standards

- When citing evidence, **strictly use the identifiers actually returned in the retrieval results**, cited verbatim, without fabricating or renumbering
- If retrieval results do not provide clear identifiers, cite by summarizing the source content of the evidence
- In the `source_indices` parameter of `next_hop` and `finalize`, accurately fill in the evidence identifiers actually referenced in the current step
- Only cite evidence that was actually used

## Handling Insufficient Evidence

| Evidence Status | Output Strategy |
|---------|---------|
| Evidence sufficient for all hops and reasoning chain complete | Output complete answer and reasoning path normally |
| Evidence sufficient for some hops, insufficient for others | Output the reasoning path supported by existing evidence, clearly indicating which parts have insufficient evidence or uncertainty |
| Critical parts severely lack evidence, reasoning chain broken | Honestly state that complete reasoning cannot be accomplished, show the partial reasoning completed and limited information collected |
"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def multi_hop_entry_node(state: MultiHopState) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    reasoning_plan = sub_query.get("reasoning_chain", [])
    if not reasoning_plan:
        reasoning_plan = [sub_query.get("query_text", "")]
    message_content = f"Answer: {sub_query.get('query_text', '')}\nReference query steps:\n{'\n'.join(reasoning_plan)}"
    info(f"[multi_hop] Entry node for: {sub_query.get('query_text', '')[:50]}...")
    return {
        "messages": [
            HumanMessage(
                content=message_content,
                additional_kwargs=agent_metadata(MultiHopNodeNames.ENTRY.value),
            )
        ],
        "reasoning_plan": reasoning_plan,
        "current_step": 0,
        "current_hop": 0,
        "intermediate_results": [],
        "intermediate_answers": [],
        "reasoning_chain": [],
        "retrieval_results": {"mode": "RESET", "data": []},
        "result_counter": 0,
    }


def multi_hop_error_node(state: MultiHopState, error_msg: str) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    error(f"[multi_hop] Error node: {error_msg}")
    return {
        "sub_answers": [
            SubAnswer(
                sub_query_id=sub_query.get("query_id", "unknown"),
                sub_query_text=sub_query.get("query_text", ""),
                query_type="multi-hop",
                answer=f"Error: {error_msg}",
                reasoning_chain=[],
                intermediate_answers=[],
                sources=[],
                confidence=0.0,
                retrieval_results=[],
            )
        ]
    }


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


async def build_multi_hop_agent_graph(
    *,
    override: AgentOverride | None = None,
    llm_service: LLMService,
    checkpointer: Any | None = None,
):
    """Build the configurable multi-hop agent graph."""
    override = override or AgentOverride()
    llm = await llm_service._get_streaming_model("retrieval")
    tools = [next_hop, finalize] + list(override.tools)
    middleware = [
        ToolCallGuardMiddleware(),
        DispatcherToolMiddleware(
            index_id_fn=lambda sub_query_idx, step, item_id: (
                f"{sub_query_idx}-{step}-{item_id}"
            ),
            follow_up_prompt="A retrieval has been completed. If this retrieval did not collect sufficient information, continue calling search_knowledge to collect more. Otherwise, immediately call next_hop to clean up context and proceed to the next query. If all retrievals are complete, immediately call finalize to end the multi-hop retrieval and generate the final answer.",
        ),
        *override.middleware,
    ]
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        state_schema=MultiHopState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=override.prompt or DEFAULT_MULTI_HOP_SYSTEM_PROMPT,
    )


async def build_multi_hop_subgraph(
    *,
    agent_override=None,
    summary_override=None,
    llm_service=None,
    checkpointer=None,
):
    """Build multi-hop subgraph using dedicated agent assembly."""
    if llm_service is None:
        raise ValueError("llm_service is required to build the multi-hop subgraph")
    agent_graph = await build_multi_hop_agent_graph(
        override=agent_override,
        llm_service=llm_service,
        checkpointer=checkpointer,
    )
    summary_graph = await build_multi_hop_summary_subgraph(
        llm_service=llm_service,
        override=summary_override,
        checkpointer=checkpointer,
    )

    workflow = StateGraph(MultiHopState, context_schema=QARuntimeContext)
    workflow.add_node(MultiHopNodeNames.ENTRY.value, multi_hop_entry_node)
    workflow.add_node(MultiHopNodeNames.AGENT.value, agent_graph)
    workflow.add_node(MultiHopNodeNames.SUMMARY.value, summary_graph)
    workflow.set_entry_point(MultiHopNodeNames.ENTRY.value)
    workflow.add_edge(MultiHopNodeNames.ENTRY.value, MultiHopNodeNames.AGENT.value)
    workflow.add_edge(MultiHopNodeNames.AGENT.value, MultiHopNodeNames.SUMMARY.value)
    workflow.add_edge(MultiHopNodeNames.SUMMARY.value, END)
    compiled = workflow.compile(checkpointer=checkpointer)
    info("[multi_hop] Compiled multi-hop subgraph with streaming support")
    return compiled


__all__ = [
    "DEFAULT_MULTI_HOP_SYSTEM_PROMPT",
    "MultiHopNodeNames",
    "MultiHopState",
    "build_multi_hop_agent_graph",
    "build_multi_hop_subgraph",
    "finalize",
    "multi_hop_entry_node",
    "multi_hop_error_node",
    "next_hop",
]
