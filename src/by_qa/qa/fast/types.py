"""Types for the fast QA capability."""

from enum import Enum

from by_qa.qa.agents.answer_synthesizer import AnswerNodeNames
from by_qa.qa.agents.standalone_question_rewriter import RewriterNodeNames


class NodeNames(str, Enum):
    """Fast QA graph node names."""

    REWRITE = "rewrite"
    RETRIEVE = "retrieve"
    ANSWER = "answer"


class AgentNames(str, Enum):
    """Agent names for the fast QA engine configuration."""

    REWRITER = RewriterNodeNames.AGENT.value
    ANSWER = AnswerNodeNames.AGENT.value


__all__ = ["AgentNames", "NodeNames"]
