"""Shared QA runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.services.llm_service import LLMService


@dataclass
class KnowledgeBaseConfig:
    """Runtime knowledge-base endpoint configuration."""

    kb_code: str
    kb_name: str
    service_name: str
    kb_description: str | None = None
    headers: dict[str, str] | None = None
    operations: dict[OperationType, str] = field(default_factory=dict)


@dataclass
class QARetrievalConfig:
    """Runtime retrieval configuration shared by QA engines."""

    knowledge_bases: list[KnowledgeBaseConfig] = field(default_factory=list)
    source_codes: list[str] | None = None
    type_codes: list[str] | None = None
    top_k: int = 20
    vector_top_k: int = 40
    text_top_k: int = 30

    def __post_init__(self) -> None:
        normalized: list[KnowledgeBaseConfig] = []
        for kb in self.knowledge_bases:
            if isinstance(kb, KnowledgeBaseConfig):
                normalized.append(kb)
                continue
            if isinstance(kb, dict):
                kb = dict(kb)
                raw_ops = kb.pop("operations", {})
                ops = {
                    OperationType(k) if isinstance(k, str) else k: v
                    for k, v in raw_ops.items()
                }
                normalized.append(KnowledgeBaseConfig(**kb, operations=ops))
                continue
            raise TypeError(
                "knowledge_bases entries must be dict or KnowledgeBaseConfig"
            )
        for kb in normalized:
            if not kb.service_name:
                raise ValueError(
                    "knowledge_bases entries must define a non-empty service_name"
                )
        self.knowledge_bases = normalized


@dataclass
class QAEngineConfig:
    """Code-level configuration shared by QA engines."""

    llm_service: LLMService | None = None
    tools: list[Any] = field(default_factory=list)
    tool_providers: dict[str, Callable[..., list[Any]]] = field(default_factory=dict)
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    prompt_builders: dict[str, Callable[..., str]] = field(default_factory=dict)
    node_callbacks: dict[str, Any] = field(default_factory=dict)
    agent_callbacks: dict[str, Any] = field(default_factory=dict)
    agent_middleware: dict[str, list[Any]] = field(default_factory=dict)
    retrieval: QARetrievalConfig = field(default_factory=QARetrievalConfig)


__all__ = [
    "KnowledgeBaseConfig",
    "QAEngineConfig",
    "QARetrievalConfig",
]
