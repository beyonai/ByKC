"""Answer node for the fast QA graph."""

import time
from typing import Any

from langchain_core.messages import AIMessage

from by_qa.qa.agents.answer_synthesizer import RetrievedContextAnswerSynthesizerAgent
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.state import FastQAState

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None  # type: ignore[assignment,misc]


async def answer_node(
    state: FastQAState,
    runtime: Runtime[QARuntimeContext] = None,
) -> dict[str, Any]:
    """Synthesize the final fast QA answer."""
    if (
        runtime is None
        or runtime.context is None
        or runtime.context.llm_service is None
    ):
        raise RuntimeError("llm_service is required for answer_node")
    start_time = time.time()
    answer = await RetrievedContextAnswerSynthesizerAgent(
        llm_service=runtime.context.llm_service
    ).answer(
        original_query=state["original_query"],
        rewritten_query=state.get("rewritten_query") or state["original_query"],
        retrieval_results=state.get("retrieval_results", []),
    )
    return {
        "final_answer": answer,
        "messages": [AIMessage(content=answer)],
        "answer_time": time.time() - start_time,
    }


__all__ = ["answer_node"]
