"""LangGraph runtime context definitions for the instant-search capability."""

from dataclasses import dataclass

from by_qa.qa.instant.config import InstantSearchRetrievalConfig


@dataclass
class InstantSearchRuntimeContext:
    """Run-scoped dependencies injected via LangGraph's native context support."""

    retrieval: InstantSearchRetrievalConfig


__all__ = ["InstantSearchRuntimeContext"]
