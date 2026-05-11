"""Query embedding service for knowledge-base retrieval."""

from __future__ import annotations

from typing import Any

import httpx

from by_qa.core.model_config import (
    EnvModelConfigProvider,
    LLMModelProfile,
    ModelConfigProvider,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


class EmbeddingQueryService:
    """Generate query embeddings from the configured embedding endpoint."""

    def __init__(
        self,
        *,
        provider: ModelConfigProvider | None = None,
        timeout: float = 30.0,
    ):
        self._provider = provider or EnvModelConfigProvider()
        self.timeout = timeout

    async def embed_query(self, query: str) -> list[float]:
        """Embed a search query using an OpenAI-compatible embedding API."""
        config = await self._provider.get_config(LLMModelProfile.EMBEDDING)
        base_url = config.base_url.rstrip("/")
        if not base_url:
            raise KnowledgeBaseConfigurationError(
                "EMBEDDING_BASE_URL is required for retrieval"
            )

        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{base_url}/embeddings",
                    headers=headers,
                    json={"model": config.model_name, "input": query},
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
