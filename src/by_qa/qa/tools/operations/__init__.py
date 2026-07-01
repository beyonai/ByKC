"""Parallel-dispatch operation base and implementations."""

from by_qa.qa.tools.operations.base import (
    BaseOperation,
    DispatchRequest,
    _normalize_headers,
)
from by_qa.qa.tools.operations.knowledge_search import KnowledgeSearchOperation

__all__ = [
    "BaseOperation",
    "DispatchRequest",
    "KnowledgeSearchOperation",
    "_normalize_headers",
]
