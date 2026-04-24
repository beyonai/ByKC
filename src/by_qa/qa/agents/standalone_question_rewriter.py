"""Rewrite and split follow-up questions into standalone sub-questions."""

from by_qa.qa.services.llm_service import LLMService

DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT = """你是一个问题改写助手。结合用户历史输入，完成以下两件事：
1. 补全当前输入中省略的主语、对象、时间等上下文
2. 如果补全后的问题包含并列的独立子问题，将其拆分为多个完整问题

要求：
- 只识别并列结构（去掉连接词后能拆出两个以上语义完整且互相独立的问题）
- 不拆分链式修饰结构（A的B的C是单一问题）
- 不分析推理深度
- 不回答问题
- 每行输出一个完整问题，不输出编号或解释
- 如果当前输入已完整且无并列结构，原样输出一行"""


class StandaloneQuestionRewriterAgent:
    """Rewrite the current query using conversation history, splitting parallel questions."""

    def __init__(
        self,
        llm_service: LLMService,
        system_prompt: str | None = None,
    ) -> None:
        self._llm_service = llm_service
        self._system_prompt = (
            system_prompt or DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT
        )

    async def rewrite_and_split(
        self, query: str, conversation_history: str | None = None
    ) -> list[str]:
        """Return a list of standalone sub-questions for retrieval."""
        if conversation_history:
            user_content = (
                "用户历史输入：\n"
                f"{conversation_history}\n\n"
                f"当前用户输入：{query}\n\n"
                "请输出改写后的问题，每行一个。"
            )
        else:
            user_content = f"当前用户输入：{query}\n\n请输出改写后的问题，每行一个。"
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = await self._llm_service.generate(
                messages=messages,
                model_type="classifier",
                json_mode=False,
            )
            lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
            return lines if lines else [query]
        except Exception:  # pylint: disable=broad-exception-caught
            return [query]


__all__ = [
    "DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT",
    "StandaloneQuestionRewriterAgent",
]
