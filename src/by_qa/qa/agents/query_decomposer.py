"""Enhanced query decomposer with hop type analysis for multi-hop question answering."""

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Dict, Literal, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.config import get_settings
from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata, extract_user_query_history
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


SYSTEM_PROMPT_WITH_HISTORY = """
你是一个查询分解助手。请结合对话历史，对用户查询进行**语法级别**的拆分。

## 唯一拆分标准：原文是否存在并列结构

只有当原文中**显式出现多个并列的查询目标**时才拆分，否则一律输出单个子查询。

**并列结构判断标准**：去掉连接词后，能拆出两个或以上**语义完整且互相独立**的问题。

| 输入 | 是否拆分 | 原因 |
|------|----------|------|
| A和B的营收各是多少 | ✅ 拆分 | 两个独立查询目标 |
| 2025年和2026年的数据 | ✅ 拆分 | 两个独立时间维度 |
| A和B哪个更好 | ❌ 不拆分 | 比较本身是一个完整问题 |
| 怎么报销发票 | ❌ 不拆分 | 单一问题 |
| 苹果CEO妻子的年龄 | ❌ 不拆分 | 单一问题，链式修饰结构 |

> **关键区分**：链式修饰结构（A的B的C）是单一问题的内部推理路径，不是并列结构，不拆分。

## 跳数标注

跳数是单个子查询**内部**的推理深度，与子查询数量无关。

- **single-hop**：可从单一来源直接获取答案
- **multi-hop**：需经过多个中间实体的链式推理，链条中每个中间实体算一跳

**hop_count 计算方式**：数链式结构中的箭头数量
- "Python最新版本" → 直接查询 → hop_count=1
- "苹果CEO的妻子的年龄" → 苹果→CEO→妻子→年龄，3个箭头 → hop_count=3
- "25年GDP最大国家的首都经纬度" → GDP排名→国家→首都→经纬度，3个箭头 → hop_count=3

## 多轮对话补全

结合上文补全省略的主语或话题，再按上述规则判断是否拆分。
## 输出格式

```json
{{
  "sub_queries": [
    {{
      "query_id": "sq_1",
      "query_text": "补全后的完整查询文本",
      "query_type": "single-hop 或 multi-hop",
      "hop_count": 1,
      "reasoning_chain": []
    }}
  ],
  "reasoning": "一句话说明：是否有并列结构、是否拆分、跳数判断依据"
}}
```

- `reasoning_chain`：single-hop 时为空数组；multi-hop 时列出推理链各步骤
- 最多生成 {max_sub_queries} 个子查询

## 示例

**① 并列时间 → 拆分，single-hop**
输入：`2025年和2026年的公司营收`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "2025年的公司营收是多少", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}},
    {{"query_id": "sq_2", "query_text": "2026年的公司营收是多少", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "原文包含两个并列时间维度（2025年、2026年），拆分为两个独立 single-hop 查询"
}}
```

**② 链式修饰结构 → 不拆分，multi-hop**
输入：`25年GDP最大的国家的首都的经纬度是？`
```json
{{
  "sub_queries": [
    {{
      "query_id": "sq_1",
      "query_text": "苹果CEO妻子的年龄",
      "query_type": "multi-hop",
      "hop_count": 3,
      "reasoning_chain": [
        "第一步：找出苹果CEO是谁",
        "第二步：找出该CEO的妻子是谁",
        "第三步：查询其妻子的年龄"
      ]
    }}
  ],
  "reasoning": "原文是链式修饰结构（苹果→CEO→妻子→年龄），无并列结构，不拆分，内部推理3跳标注为 multi-hop"
}}
```
**③ 并列对象 → 拆分，single-hop**
输入：`豆包和千问的核心竞争力分别是什么`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "豆包的核心竞争力是什么", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}},
    {{"query_id": "sq_2", "query_text": "千问的核心竞争力是什么", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "原文包含两个并列对象（豆包、千问），拆分为两个独立 single-hop 查询"
}}
```

**④ 单一问题 → 不拆分，single-hop**
输入：`怎么报销发票`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "怎么报销发票", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "原文是单一完整问题，无并列结构，不拆分"
}}
```

**⑤ 多轮对话补全**
对话历史：用户问南京办事处营收，助手已回答
输入：`广州呢`
```json
{{
  "sub_queries": [
    {{"query_id": "sq_1", "query_text": "广州办事处的营收是多少", "query_type": "single-hop", "hop_count": 1, "reasoning_chain": []}}
  ],
  "reasoning": "结合上文补全省略主语，'广州呢'指广州办事处的营收，单一问题不拆分"
}}
```

**⑥ single-hop 与 multi-hop 并列 → 拆分**
输入：`2025年公司总营收是多少？以及销售额最高的产品的研发负责人是谁？`
```json
{{
  "sub_queries": [
    {{
      "query_id": "sq_1",
      "query_text": "2025年公司总营收是多少",
      "query_type": "single-hop",
      "hop_count": 1,
      "reasoning_chain": []
    }},
    {{
      "query_id": "sq_2",
      "query_text": "销售额最高的产品的研发负责人是谁",
      "query_type": "multi-hop",
      "hop_count": 2,
      "reasoning_chain": [
        "第一步：找出销售额最高的产品",
        "第二步：找出该产品的研发负责人"
      ]
    }}
  ],
  "reasoning": "原文包含两个并列且独立的问题，拆分为两个子查询；第一个直接可查为 single-hop，第二个需链式推理为 multi-hop"
}}
```
"""


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
        result = json.loads(response)
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
    except json.JSONDecodeError:
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
        "用户历史输入：\n"
        f"{conversation_history if conversation_history else '无历史输入'}\n\n"
        f"当前用户输入：{original_query}\n"
        f"请将其分解为最多{max_sub_queries}个子查询。"
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
                "content": f"已将问题分解为 {len(result.sub_queries)} 个子查询 "
                f"({single_hop_count} 单跳, {multi_hop_count} 多跳)",
            }
        ],
    }


async def build_decomposer_subgraph(
    *,
    llm_service: LLMService,
    system_prompt: str | None = None,
    checkpointer=None,
):
    """Build the decomposer subgraph: entry → create_agent → summary."""
    max_sub_queries = get_settings().decomposer_max_sub_queries
    prompt = (system_prompt or SYSTEM_PROMPT_WITH_HISTORY).replace(
        "{max_sub_queries}", str(max_sub_queries)
    )
    llm = await llm_service._get_streaming_model("classifier")
    llm = llm.bind(response_format={"type": "json_object"})

    agent_graph = create_agent(
        model=llm,
        tools=[],
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
