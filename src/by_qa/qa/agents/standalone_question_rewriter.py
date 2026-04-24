"""Rewrite follow-up questions into standalone questions."""

from by_qa.qa.services.llm_service import LLMService

DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT = """你是一个问题改写助手。

你的任务是结合用户历史输入，将当前用户输入改写成一个可以独立理解的完整问题。

要求：
- 只补全历史上下文中明确存在的主语、对象、时间、范围或约束
- 不回答问题
- 不拆分问题
- 不改变用户真实意图
- 如果当前输入已经完整，原样返回
- 如果历史不足以补全，原样返回当前输入

只输出改写后的问题文本，不要输出解释。"""


class StandaloneQuestionRewriterAgent:
    """Rewrite the current query using conversation history."""

    def __init__(
        self,
        llm_service: LLMService,
        system_prompt: str | None = None,
    ) -> None:
        self._llm_service = llm_service
        self._system_prompt = (
            system_prompt or DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT
        )

    async def rewrite(self, query: str, conversation_history: str | None = None) -> str:
        """Return a standalone question for retrieval."""
        if not conversation_history:
            return query
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": (
                    "用户历史输入：\n"
                    f"{conversation_history}\n\n"
                    f"当前用户输入：{query}\n\n"
                    "请输出改写后的完整问题。"
                ),
            },
        ]
        rewritten = await self._llm_service.generate(
            messages=messages,
            model_type="classifier",
            json_mode=False,
        )
        return rewritten.strip() or query


__all__ = [
    "DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT",
    "StandaloneQuestionRewriterAgent",
]
