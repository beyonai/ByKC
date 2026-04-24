"""Answer synthesis from retrieved context."""

from typing import Any

from by_qa.qa.common.context_manager import build_context_for_llm
from by_qa.qa.services.llm_service import LLMService

DEFAULT_RETRIEVED_CONTEXT_ANSWER_PROMPT = """你是一个严谨的知识库问答助手。

你的任务是基于给定检索结果回答用户问题。

要求：
- 直接回答问题，保持简洁清晰
- 只能使用检索结果中的信息，不要编造
- 如果检索结果不足以回答，请明确说明缺少相关信息
- 如有必要，可以简要列出依据
- 直接输出 Markdown 文本，不要输出 JSON"""


class RetrievedContextAnswerSynthesizerAgent:
    """Synthesize a final answer from retrieval results."""

    def __init__(
        self,
        llm_service: LLMService,
        system_prompt: str | None = None,
    ) -> None:
        self._llm_service = llm_service
        self._system_prompt = system_prompt or DEFAULT_RETRIEVED_CONTEXT_ANSWER_PROMPT

    async def answer(
        self,
        *,
        original_query: str,
        rewritten_query: str,
        retrieval_results: list[dict[str, Any]],
    ) -> str:
        """Generate an answer grounded in retrieval results."""
        context = build_context_for_llm(retrieval_results)
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": f"""用户原始问题：{original_query}
检索用完整问题：{rewritten_query}

检索结果：
{context}

请基于以上检索结果回答用户原始问题。""",
            },
        ]
        return await self._llm_service.generate(
            messages=messages,
            model_type="generator",
            json_mode=False,
        )


__all__ = [
    "DEFAULT_RETRIEVED_CONTEXT_ANSWER_PROMPT",
    "RetrievedContextAnswerSynthesizerAgent",
]
