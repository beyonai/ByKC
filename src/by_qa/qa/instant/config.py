"""User-facing configuration for the instant QA capability."""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class KnowledgeBaseConfig:
    """Runtime knowledge-base endpoint configuration."""

    kb_code: str
    kb_name: str
    kb_url: str
    kb_description: str | None = None


@dataclass
class InstantQARetrievalConfig:
    """Runtime retrieval configuration injected into the instant QA runtime."""

    knowledge_bases: list[KnowledgeBaseConfig] = field(default_factory=list)
    source_codes: list[str] | None = None
    type_codes: list[str] | None = None
    top_k: int = 10
    vector_top_k: int = 40
    text_top_k: int = 30

    def __post_init__(self) -> None:
        normalized_knowledge_bases: list[KnowledgeBaseConfig] = []
        for knowledge_base in self.knowledge_bases:
            if isinstance(knowledge_base, KnowledgeBaseConfig):
                normalized_knowledge_bases.append(knowledge_base)
                continue
            if isinstance(knowledge_base, dict):
                normalized_knowledge_bases.append(KnowledgeBaseConfig(**knowledge_base))
                continue
            raise TypeError(
                "knowledge_bases entries must be dict or KnowledgeBaseConfig"
            )
        for knowledge_base in normalized_knowledge_bases:
            if not knowledge_base.kb_url:
                raise ValueError(
                    "knowledge_bases entries must define a non-empty kb_url"
                )
        self.knowledge_bases = normalized_knowledge_bases


@dataclass
class InstantQAConfig:
    """Code-level configuration for instantiating instant QA."""

    model: str | None = None
    llm_factory: Callable[..., Any] | None = None
    tools: list[Any] = field(default_factory=list)
    tool_providers: dict[str, Callable[..., list[Any]]] = field(default_factory=dict)
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    prompt_builders: dict[str, Callable[..., str]] = field(default_factory=dict)
    node_callbacks: dict[str, Any] = field(default_factory=dict)
    agent_callbacks: dict[str, Any] = field(default_factory=dict)
    agent_middleware: dict[str, list[Any]] = field(default_factory=dict)
    retrieval: InstantQARetrievalConfig = field(
        default_factory=InstantQARetrievalConfig
    )


InstantSearchAgentConfig = InstantQAConfig
InstantSearchRetrievalConfig = InstantQARetrievalConfig


__all__ = [
    "InstantQAConfig",
    "InstantQARetrievalConfig",
    "InstantSearchAgentConfig",
    "InstantSearchRetrievalConfig",
    "KnowledgeBaseConfig",
]
