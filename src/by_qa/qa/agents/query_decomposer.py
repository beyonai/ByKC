"""Enhanced query decomposer with hop type analysis for multi-hop question answering."""

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Dict, Literal, Optional, TypedDict

import json_repair
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.config import get_settings
from by_qa.core.logger import info
from by_qa.core.model_config import LLMModelProfile
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata, extract_user_query_history
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.services.llm_service import LLMService


@dataclass
class SubQuery:
    """Enhanced sub-query with hop type annotation."""

    query_id: str
    query_text: str
    query_type: Literal["single-hop", "multi-hop"]
    hop_count: int
    dependencies: list[str]
    reasoning_chain: list[str] | None = None


@dataclass
class DecompositionResult:
    """Result of query decomposition."""

    sub_queries: list[SubQuery]
    reasoning: str
    metadata: dict


SYSTEM_PROMPT_WITH_HISTORY = (
    """
You are a query analysis assistant. Your **default output is a single sub-query** — preserve the original question as one unit and annotate its internal reasoning depth (hop count).

**Splitting is the rare exception.** Only split when the original text contains explicit parallel structures. When in doubt, do NOT split.

## Execution Model (why this matters)

Each sub-query is dispatched to an **independent agent in parallel**. Agents cannot see each other's results. This means:

- A sub-query must be **self-contained and answerable on its own** — no placeholders, no references to other sub-queries' outputs
- If sub-query B depends on sub-query A's result to be answerable, **they must NOT be split** — keep them as one multi-hop sub-query with a reasoning_chain
- The reasoning_chain field documents the internal steps an agent should follow sequentially within a single sub-query

## When to Split (the ONLY criterion): Explicit Parallel Structures

Split ONLY when removing a conjunction yields two or more **semantically complete and mutually independent** questions — each answerable **in isolation** without the other's result.

| Input | Split? | Reason |
|-------|--------|--------|
| Revenue of A and B | Yes | Two independent query targets |
| Data for 2025 and 2026 | Yes | Two independent time dimensions |
| Which is better, A or B | No | The comparison itself is one complete question |
| How to reimburse an invoice | No | Single question |
| Age of Apple CEO's wife | No | Single question, chained modifier structure |
| GDP of the country whose leader won prize X | No | Clause result feeds into main query |

> **Key distinction**: If one clause's answer becomes a required parameter for another clause, they form a **dependency chain** — do not split.

### Dependency Chain Recognition (→ NEVER split, output as single multi-hop sub-query)

A dependency chain exists whenever the query contains clauses where **the output of one becomes the input of another**. When you detect a dependency chain, you MUST output exactly one sub-query with the full original question and annotate the reasoning_chain internally. **Do NOT decompose a chain into multiple sub-queries — that defeats the purpose of hop annotation.**

Common patterns:

1. **Possessive chain** (A's B's C): Each entity resolves to the next
2. **Conditional/temporal dependency** ("X at the time when Y", "X of the thing that Y"): The subordinate clause resolves to an intermediate entity (a time, a place, an object) that the main clause requires
3. **Relative clause dependency** ("the X of [entity satisfying condition Z]"): The condition must be resolved first to identify the entity
4. **Hypothetical/definitional premise** ("Imagine X whose value equals Y", "If X is the same as Y"): The equivalence is a **given**, not a separate information need — collapse it into the chain as a known mapping, do not generate a separate sub-query for it

**Litmus test**: Can each clause be answered **independently without referencing the other's result**? If not → chain, do not split.

> **Premise ≠ Query**: When the question explicitly defines an equivalence (e.g., "a building whose height equals number X"), that definition is a premise to carry forward, not a fact to look up. Only count actual information retrieval steps as hops.

## Hop Count Annotation

Hop count is the **internal** reasoning depth of a single sub-query, unrelated to the number of sub-queries.

- **single-hop**: Answer can be obtained directly from a single source
- **multi-hop**: Requires chained reasoning through multiple intermediate entities, each intermediate entity counts as one hop

**hop_count calculation**: Count the number of **actual information retrieval** arrows in the chain
- "Latest version of Python" → direct query → hop_count=1
- "Age of Apple CEO's wife" → Apple→CEO→wife→age, 3 arrows → hop_count=3
- "Coordinates of the capital of the country with highest GDP in 2025" → GDP ranking→country→capital→coordinates, 3 arrows → hop_count=3
- "Population of the city that hosted the event when X happened" → X→event time→host city→population, 3 arrows → hop_count=3
- "Imagine Y equals the value of X; where does Y rank in list Z" → find X→rank in Z, 2 arrows → hop_count=2 (the definitional equivalence Y=X is a premise, not a hop)

## Multi-turn Conversation Completion

Complete omitted subjects or topics based on context, then apply the above rules to determine whether to split.

## Decision Process (follow in order)

1. **Complete** the query if context is missing (multi-turn)
2. **Check for dependency**: Does any clause's result feed into another? → YES: output as **one** multi-hop sub-query, annotate reasoning_chain. STOP.
3. **Check for parallel structure**: Does removing a conjunction yield 2+ independent, self-sufficient questions? → YES: split into separate sub-queries.
4. **Verify isolation**: For each candidate sub-query, ask: "Can an agent answer this without seeing any other sub-query's result?" If NO → merge back into one.
5. **Default**: Output as one sub-query.

## Output Format

```json
{{
  "sub_queries": [
    {{
      "query_id": "sq_1",
      "query_text": "Complete query text after completion",
      "query_type": "single-hop or multi-hop",
      "hop_count": 1,
      "reasoning_chain": []
    }}
  ],
  "reasoning": "One sentence explaining: whether parallel structure exists, whether to split, hop count rationale. Must be in the same language as the user's current input."
}}
```

- `reasoning_chain`: Empty array for single-hop; list reasoning chain steps for multi-hop
- Generate at most {max_sub_queries} sub-queries

## Examples

**1. Parallel time → split, single-hop**
Input: `Revenue for 2025 and 2026`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "What is the company revenue for 2025", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}},
    {{"query_id": "sq_2", "query_text": "What is the company revenue for 2026", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "Original text contains two parallel time dimensions (2025, 2026), split into two independent single-hop queries"
}}
```

**2. Conditional dependency chain → no split, multi-hop**
Input: `What is the GDP of the country whose leader won the most recent Nobel Peace Prize?`
```json
{{
  "sub_queries": [
    {{
      "query_id": "sq_1",
      "query_text": "What is the GDP of the country whose leader won the most recent Nobel Peace Prize?",
      "query_type": "multi-hop",
      "hop_count": 3,
      "reasoning_chain": [
        "Step 1: Find who won the most recent Nobel Peace Prize",
        "Step 2: Determine which country that leader represents",
        "Step 3: Look up the GDP of that country"
      ]
    }}
  ],
  "reasoning": "The relative clause 'whose leader won the most recent Nobel Peace Prize' must resolve first to identify the country; this is a conditional dependency chain, not parallel structure, no split, 3-hop"
}}
```

**3. Parallel objects → split, single-hop**
Input: `What are the core competencies of Doubao and Qwen respectively`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "What is Doubao's core competency", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}},
    {{"query_id": "sq_2", "query_text": "What is Qwen's core competency", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "Original text contains two parallel objects (Doubao, Qwen), split into two independent single-hop queries"
}}
```

**4. Single question → no split, single-hop**
Input: `How to reimburse an invoice`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "How to reimburse an invoice", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "Original text is a single complete question, no parallel structure, no split"
}}
```

**5. Multi-turn conversation completion**
Conversation history: User asked about Nanjing office revenue, assistant already answered
Input: `What about Guangzhou`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "What is the revenue of the Guangzhou office", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "Completed omitted subject based on context, 'What about Guangzhou' refers to Guangzhou office revenue, single question no split"
}}
```

**6. Temporal dependency + parallel → partial split**
Input: `What is the total company revenue for 2025? And what is the population of the city where our annual conference was held that year?`
```json
{{
  "sub_queries": [
    {{
      "query_id": "sq_1",
      "query_text": "What is the total company revenue for 2025",
      "query_type": "single-hop",
      "hop_count": 1,
      "reasoning_chain": []
    }},
    {{
      "query_id": "sq_2",
      "query_text": "What is the population of the city where the company annual conference was held in 2025?",
      "query_type": "multi-hop",
      "hop_count": 2,
      "reasoning_chain": [
        "Step 1: Find which city hosted the annual conference in 2025",
        "Step 2: Look up the population of that city"
      ]
    }}
  ],
  "reasoning": "Two independent questions (parallel structure, split); the first is direct single-hop; the second contains a conditional dependency (conference city → population), marked as multi-hop with 2-hop chain"
}}
```
"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


class DecomposerNodeNames(str, Enum):
    ENTRY = "decomposer_entry"
    AGENT = "decomposer_agent"
    SUMMARY = "decomposer_summary"


class DecomposerAgentState(TypedDict):
    """State for the decomposer subgraph."""

    messages: Annotated[list, add_messages]
    original_query: str
    sub_queries: list[dict]
    decomposition_metadata: Optional[dict]
    decomposition_time: Optional[float]


def _generate_metadata(sub_queries: list[SubQuery]) -> dict:
    """Generate metadata about the decomposition result."""
    total = len(sub_queries)
    single_hop_count = sum(1 for sq in sub_queries if sq.query_type == "single-hop")
    multi_hop_count = sum(1 for sq in sub_queries if sq.query_type == "multi-hop")
    has_dependencies = any(bool(sq.dependencies) for sq in sub_queries)
    multi_hop_queries = [sq for sq in sub_queries if sq.query_type == "multi-hop"]
    avg_hop_count = (
        sum(sq.hop_count for sq in multi_hop_queries) / len(multi_hop_queries)
        if multi_hop_queries
        else 0
    )
    return {
        "total_sub_queries": total,
        "single_hop_count": single_hop_count,
        "multi_hop_count": multi_hop_count,
        "has_dependencies": has_dependencies,
        "avg_hop_count": round(avg_hop_count, 2) if avg_hop_count > 0 else None,
    }


def _parse_decomposition_response(
    response: str, fallback_query: str, max_sub_queries: int
) -> DecompositionResult:
    """Parse the LLM JSON response into a DecompositionResult."""
    try:
        result = json_repair.loads(response)
        if not isinstance(result, dict):
            raise ValueError("response is not a JSON object")
        sub_queries_data = result.get("sub_queries", [])
        reasoning = result.get("reasoning", "")
        normalized_queries: list[SubQuery] = []
        for index, sq_data in enumerate(sub_queries_data[:max_sub_queries], 1):
            if isinstance(sq_data, str):
                normalized_queries.append(
                    SubQuery(
                        query_id=str(index),
                        query_text=sq_data,
                        query_type="single-hop",
                        hop_count=1,
                        dependencies=[],
                        reasoning_chain=[],
                    )
                )
                continue
            normalized_queries.append(
                SubQuery(
                    query_id=sq_data.get("query_id", str(index)),
                    query_text=sq_data.get("query_text", ""),
                    query_type=sq_data.get("query_type", "single-hop"),
                    hop_count=sq_data.get("hop_count", 1),
                    dependencies=sq_data.get("dependencies", []),
                    reasoning_chain=sq_data.get("reasoning_chain", []),
                )
            )
        return DecompositionResult(
            sub_queries=normalized_queries,
            reasoning=reasoning,
            metadata=_generate_metadata(normalized_queries),
        )
    except (json.JSONDecodeError, ValueError):
        fallback_queries = [
            SubQuery(
                query_id="1",
                query_text=fallback_query,
                query_type="single-hop",
                hop_count=1,
                dependencies=[],
                reasoning_chain=[],
            )
        ]
        return DecompositionResult(
            sub_queries=fallback_queries,
            reasoning="Failed to parse decomposition result, fallback to single query",
            metadata=_generate_metadata(fallback_queries),
        )


async def decomposer_entry_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Entry node: build the HumanMessage for the decomposer agent."""
    max_sub_queries = get_settings().decomposer_max_sub_queries
    original_query = state["original_query"]
    messages = state.get("messages", [])
    conversation_history = extract_user_query_history(messages, max_turns=5)
    if conversation_history:
        info(
            f"[decomposer] Using {len(conversation_history.split(chr(10)))} "
            "previous user queries"
        )
    user_content = (
        "User history:\n"
        f"{conversation_history if conversation_history else 'No history'}\n\n"
        f"Current user input: {original_query}\n"
        f"Decompose into at most {max_sub_queries} sub-queries."
    )
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=user_content,
                additional_kwargs=agent_metadata(DecomposerNodeNames.ENTRY.value),
            ),
        ],
        "sub_queries": [],
        "decomposition_metadata": None,
    }


async def decomposer_summary_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Summary node: parse the agent response into sub_queries."""
    max_sub_queries = get_settings().decomposer_max_sub_queries
    original_query = state["original_query"]
    start_time = state.get("decomposition_time", time.time())
    messages = state.get("messages", [])

    response_text = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "content", ""):
            response_text = msg.content
            break
        if isinstance(msg, dict) and msg.get("type") == "ai" and msg.get("content"):
            response_text = msg["content"]
            break

    result = _parse_decomposition_response(
        response_text, original_query, max_sub_queries
    )
    decomposition_time = time.time() - start_time
    single_hop_count = sum(
        1 for sq in result.sub_queries if sq.query_type == "single-hop"
    )
    multi_hop_count = sum(
        1 for sq in result.sub_queries if sq.query_type == "multi-hop"
    )
    info(
        f"[decomposer] Generated {len(result.sub_queries)} sub-queries "
        f"({single_hop_count} single-hop, {multi_hop_count} multi-hop) "
        f"in {decomposition_time:.2f}s"
    )
    sub_queries_dicts = [
        {
            "query_id": sq.query_id,
            "query_text": sq.query_text,
            "query_type": sq.query_type,
            "hop_count": sq.hop_count,
            "dependencies": sq.dependencies,
            "reasoning_chain": sq.reasoning_chain or [],
        }
        for sq in result.sub_queries
    ]
    return {
        "sub_queries": sub_queries_dicts,
        "decomposition_metadata": result.metadata,
        "decomposition_time": decomposition_time,
        "messages": [
            {
                "role": "assistant",
                "content": f"Decomposed into {len(result.sub_queries)} sub-queries "
                f"({single_hop_count} single-hop, {multi_hop_count} multi-hop)",
            }
        ],
    }


async def build_decomposer_subgraph(
    *,
    llm_service: LLMService,
    override: AgentOverride | None = None,
    checkpointer=None,
):
    """Build the decomposer subgraph: entry → create_agent → summary."""
    override = override or AgentOverride()
    max_sub_queries = get_settings().decomposer_max_sub_queries
    prompt = (override.prompt or SYSTEM_PROMPT_WITH_HISTORY).replace(
        "{max_sub_queries}", str(max_sub_queries)
    )
    llm = await llm_service._get_streaming_model(LLMModelProfile.LIGHTWEIGHT)
    llm = llm.bind(response_format={"type": "json_object"})

    agent_graph = create_agent(
        model=llm,
        tools=[],
        middleware=list(override.middleware),
        state_schema=DecomposerAgentState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=prompt,
    )

    async def _entry(state):
        result = await decomposer_entry_node(state)
        result["decomposition_time"] = time.time()
        return result

    workflow = StateGraph(DecomposerAgentState, context_schema=QARuntimeContext)
    workflow.add_node(DecomposerNodeNames.ENTRY.value, _entry)
    workflow.add_node(DecomposerNodeNames.AGENT.value, agent_graph)
    workflow.add_node(DecomposerNodeNames.SUMMARY.value, decomposer_summary_node)
    workflow.set_entry_point(DecomposerNodeNames.ENTRY.value)
    workflow.add_edge(DecomposerNodeNames.ENTRY.value, DecomposerNodeNames.AGENT.value)
    workflow.add_edge(
        DecomposerNodeNames.AGENT.value, DecomposerNodeNames.SUMMARY.value
    )
    workflow.add_edge(DecomposerNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "DecomposerAgentState",
    "DecompositionResult",
    "SubQuery",
    "SYSTEM_PROMPT_WITH_HISTORY",
    "_parse_decomposition_response",
    "build_decomposer_subgraph",
    "decomposer_entry_node",
    "decomposer_summary_node",
]
