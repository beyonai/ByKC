"""Sub-answer aggregator for synthesizing sub-query answers into final answer."""

from by_qa.config import get_settings
from by_qa.qa.services.llm_service import get_llm_service


class SubAnswerAggregatorAgent:
    """Aggregate sub-query answers and generate final answer to user's original question.

    This agent is different from ResultAggregatorAgent:
    - ResultAggregatorAgent: aggregates retrieval results (raw content from KB/web)
    - SubAnswerAggregatorAgent: aggregates sub-answers (processed answers from sub-queries)
    """

    SYSTEM_PROMPT = """你是一个专业的回答整合专家。你的任务是基于多个子查询的答案，生成对用户原始问题的完整回答。

## 核心要求

1. **综合回答**：整合所有子查询的答案，生成对原始问题的完整回答
2. **逻辑连贯**：确保回答逻辑清晰，各部分之间过渡自然
3. **Markdown格式**：直接输出Markdown格式的回复，不要输出JSON
4. **不添加引用**：不需要标注引用来源，专注于回答内容本身

## 回答结构

请根据子查询的数量和类型，灵活组织回答结构：

- **单个子查询**：直接呈现该子查询的答案
- **多个子查询**：
  - 如果子查询是并列关系（如"A和B的营收"），分别呈现后再给出综合结论
  - 如果子查询有依赖关系，按逻辑顺序呈现
  - 对于multi-hop子查询，简要说明推理过程

## 注意事项

1. 保持客观，不要添加子查询答案中没有的信息
2. 如果子查询答案之间有冲突，请指出并给出最可能的结论
3. 如果某些子查询未能找到答案，说明该部分信息缺失
4. 回答应该直接回应用户的原始问题"""

    def __init__(self):
        self.max_tokens = get_settings().context_max_tokens

    def _build_sub_answers_context(self, sub_answers: list[dict]) -> str:
        if not sub_answers:
            return "未找到子查询答案。"

        parts: list[str] = []
        for index, sub_answer in enumerate(sub_answers, 1):
            query_text = sub_answer.get("sub_query_text", f"子查询 {index}")
            query_type = sub_answer.get("query_type", "single-hop")
            answer = sub_answer.get("answer", "")
            reasoning_chain = sub_answer.get("reasoning_chain", [])
            confidence = sub_answer.get("confidence", 0.0)
            part = (
                f"## 子查询 {index}: {query_text}\n"
                f"类型: {query_type}\n"
                f"置信度: {confidence:.2f}\n\n"
                f"### 答案\n{answer}\n"
            )
            if reasoning_chain:
                part += "\n### 推理过程\n"
                for step in reasoning_chain:
                    part += f"- {step}\n"
            parts.append(part)
        return "\n\n---\n\n".join(parts)

    async def aggregate(
        self,
        original_query: str,
        sub_answers: list[dict],
    ) -> str:
        sub_answers_context = self._build_sub_answers_context(sub_answers)

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""用户原始问题：{original_query}

子查询答案：
{sub_answers_context}

请基于以上子查询答案，生成对用户原始问题的完整回答。""",
            },
        ]

        llm = get_llm_service()
        response = await llm.generate(
            messages=messages,
            model_type="generator",
            json_mode=False,
        )

        return response


_subanswer_aggregator = SubAnswerAggregatorAgent()


async def aggregate_sub_answers(
    original_query: str,
    sub_answers: list[dict],
) -> str:
    """Aggregate sub-answers (convenience function)."""
    return await _subanswer_aggregator.aggregate(original_query, sub_answers)
