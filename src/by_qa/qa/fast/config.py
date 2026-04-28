"""Configuration for the fast QA capability."""

from __future__ import annotations

from dataclasses import dataclass, field

from by_qa.qa.common.config import AgentOverride, QARetrievalConfig
from by_qa.qa.services.llm_service import LLMService


@dataclass
class FastQAConfig:
    """Code-level configuration for fast QA."""

    llm_service: LLMService | None = None
    retrieval: QARetrievalConfig = field(default_factory=QARetrievalConfig)
    agents: dict[str, AgentOverride] = field(default_factory=dict)
    rewrite_history_turns: int = 5

    def __post_init__(self) -> None:
        if isinstance(self.retrieval, dict):
            self.retrieval = QARetrievalConfig(**(self.retrieval.__dict__))


__all__ = ["FastQAConfig"]
