"""Shared LangGraph runtime context definitions for QA engines."""

from __future__ import annotations

from dataclasses import dataclass, field

from by_qa.qa.common.config import QARetrievalConfig
from by_qa.qa.services.llm_service import LLMService


@dataclass
class QARuntimeContext:
    """Run-scoped dependencies injected via LangGraph's native context support."""

    retrieval: QARetrievalConfig
    llm_service: LLMService | None = field(default=None)


__all__ = ["QARuntimeContext"]
