"""Query embedding service for knowledge-base retrieval."""

from __future__ import annotations

from typing import Any

import httpx

from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


class EmbeddingQueryService:
    """Generate query embeddings from the configured embedding endpoint."""

    def __init__(
        self, *, base_url: str, api_key: str, model_name: str, timeout: float = 30.0
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.timeout = timeout

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query using an OpenAI-compatible embedding API."""
        if not self.base_url:
            raise KnowledgeBaseConfigurationError(
                "EMBEDDING_BASE_URL is required for retrieval"
            )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model_name, "input": query},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KnowledgeBaseConfigurationError(
                f"embedding service request failed: {exc}"
            ) from exc
        payload: dict[str, Any] = response.json()
        data = payload.get("data") or []
        if not data or "embedding" not in data[0]:
            raise KnowledgeBaseConfigurationError(
                "embedding response did not include data[0].embedding"
            )
        return data[0]["embedding"]
