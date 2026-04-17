"""Result aggregator for synthesizing search results into answers."""

from by_qa.qa.services.llm_service import LLMService


def group_results_by_source(retrieval_results: list[dict]) -> dict[str, list[dict]]:
    """Group retrieval results by source type."""
    grouped = {"knowledge_base": [], "web": []}
    for result in retrieval_results:
        source_type = result.get("source_type", "unknown")
        grouped[source_type if source_type in grouped else "knowledge_base"].append(
            result
        )
    return grouped


def build_source_context(source_type: str, results: list[dict]) -> str:
    """Build context text for one source type."""
    if not results:
        return ""

    source_name = "知识库" if source_type == "knowledge_base" else "网络搜索"
    context_parts = [f"## {source_name}来源信息\n"]
    results_by_query: dict[str, list[dict]] = {}
    for result in results:
        query_id = result.get("sub_query_id", "unknown")
        results_by_query.setdefault(query_id, []).append(result)

    for query_id, query_results in results_by_query.items():
        del query_id
        sub_query_text = query_results[0].get("sub_query_text", "子查询")
        context_parts.append(f"### 子查询: {sub_query_text}\n")
        for index, result in enumerate(query_results, 1):
            content = result.get("content", "")
            source_info = result.get("source", "unknown")
            truncated_marker = " (已截断)" if result.get("truncated") else ""
            context_parts.append(
                f"{index}. {content}{truncated_marker}\n   来源: {source_info}\n"
            )
        context_parts.append("\n")

    return "\n".join(context_parts)


class ResultAggregatorAgent:
    """Aggregate search results and generate final answer with source separation."""

    SYSTEM_PROMPT = """你是一个专业的信息整合专家。你的任务是基于检索结果回答用户问题。

## 核心要求

1. **区分来源回答**：必须分别总结知识库和网络搜索的结果，不能混合不同来源
2. **独立成段**：每个来源的总结应该独立成段，清晰标注来源
3. **Markdown格式**：直接输出Markdown格式的回复，不要输出JSON

## 回答结构

请按以下结构组织回答：

### 知识库信息
[基于知识库检索结果的综合回答]

### 网络信息
[基于网络搜索结果的回答]

### 综合建议
[基于所有信息的补充说明或建议]

## 注意事项

1. 如果某个来源没有检索到结果，说明"未找到相关信息"
2. 如果检索结果有冲突，请指出并说明
3. 如果某些结果已被截断，请在回答中说明
4. 保持客观，不要添加检索结果中没有的信息"""

    def __init__(self, llm_service: LLMService):
        self._llm_service = llm_service

    async def aggregate(
        self,
        original_query: str,
        retrieval_results: list[dict],
        conversation_history: list[dict] | None = None,
    ) -> str:
        del conversation_history
        grouped_results = group_results_by_source(retrieval_results)
        kb_context = build_source_context(
            "knowledge_base", grouped_results["knowledge_base"]
        )
        web_context = build_source_context("web", grouped_results["web"])

        context_parts = []
        if kb_context:
            context_parts.append(kb_context)
        if web_context:
            context_parts.append(web_context)

        full_context = (
            "\n\n".join(context_parts) if context_parts else "未找到相关检索结果。"
        )

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""用户问题：{original_query}

检索结果：
{full_context}

请基于以上检索结果，区分不同来源生成Markdown格式的回答。""",
            },
        ]

        llm = self._llm_service
        response = await llm.generate(
            messages=messages,
            model_type="generator",
            json_mode=False,
        )

        return response
