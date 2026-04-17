"""LangGraph runtime context definitions for the instant-search capability."""

from __future__ import annotations

from dataclasses import dataclass, field

from by_qa.qa.instant.config import InstantSearchRetrievalConfig
from by_qa.qa.services.llm_service import LLMService


@dataclass
class InstantSearchRuntimeContext:
    """Run-scoped dependencies injected via LangGraph's native context support."""

    retrieval: InstantSearchRetrievalConfig
    llm_service: LLMService | None = field(default=None)


__all__ = ["InstantSearchRuntimeContext"]
