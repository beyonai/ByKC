"""Abstract base for parallel-dispatch operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from by_qa.qa.common.config import KnowledgeBaseConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OperationType


def _normalize_headers(headers: dict[str, Any] | None) -> dict[str, str] | None:
    if not headers:
        return None
    return {
        str(key): "" if value is None else str(value) for key, value in headers.items()
    }


@dataclass
class DispatchRequest:
    """A single HTTP request ready to be fired by the dispatcher."""

    service_name: str
    path: str
    body: dict[str, Any]
    base_url: str | None = None
    headers: dict[str, str] | None = None


class BaseOperation(ABC):
    """Abstract base for operations dispatched across multiple KBs in parallel.

    Subclasses handle request building and result post-processing.
    The dispatcher handles HTTP execution and parallel orchestration.
    """

    operation_type: ClassVar[OperationType]

    @abstractmethod
    def build_requests(
        self,
        payload: dict[str, Any],
        kbs: list[KnowledgeBaseConfig],
        ctx: QARuntimeContext,
    ) -> tuple[list[DispatchRequest], list[dict[str, Any]]]:
        """Build dispatch requests and any pre-dispatch error results.

        Returns:
            (requests_to_fire, pre_dispatch_error_entries)
        """

    @abstractmethod
    def process_response(
        self, response: dict[str, Any], request: DispatchRequest
    ) -> Any:
        """Process a successful HTTP response (resultCode == '0')."""

    @abstractmethod
    def process_api_error(
        self, response: dict[str, Any], request: DispatchRequest
    ) -> Any:
        """Process an API-level error response (resultCode != '0')."""

    @abstractmethod
    def process_error(self, exc: Exception, request: DispatchRequest) -> Any:
        """Process a transport-level exception."""

    def aggregate(self, parts: list[Any]) -> Any:
        """Combine per-request results into the final return value. Default: identity."""
        return parts


__all__ = [
    "BaseOperation",
    "DispatchRequest",
    "_normalize_headers",
]
