"""Pluggable model configuration interface for LLM and embedding services."""

from dataclasses import dataclass, field
from enum import Enum
from importlib import import_module
from os import getenv
from typing import Any, Protocol, runtime_checkable

from by_qa.config import get_settings


@dataclass
class ModelConfig:
    model_name: str
    temperature: float
    base_url: str
    api_key: str
    extra_body: dict[str, Any] = field(default_factory=dict)
    dimension: int | None = None
    distance_metric: str | None = None
    batch_max_texts: int | None = None
    max_model_len: int | None = None


class LLMModelProfile(str, Enum):
    """Supported built-in LLM configuration profiles."""

    STANDARD = "standard"
    LIGHTWEIGHT = "lightweight"
    EMBEDDING = "embedding"


@runtime_checkable
class ModelConfigProvider(Protocol):
    async def get_config(self, model_type: str | LLMModelProfile) -> ModelConfig: ...


class EnvModelConfigProvider(ModelConfigProvider):
    """Default provider that reads model config from environment variables."""

    async def get_config(self, model_type: str | LLMModelProfile) -> ModelConfig:
        settings = get_settings()
        if model_type == LLMModelProfile.STANDARD:
            return ModelConfig(
                model_name=settings.llm_standard_model,
                temperature=settings.llm_standard_temp,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                extra_body=dict(settings.llm_standard_extra_body),
                max_model_len=settings.llm_standard_max_model_len,
            )
        if model_type == LLMModelProfile.LIGHTWEIGHT:
            return ModelConfig(
                model_name=settings.llm_lightweight_model,
                temperature=settings.llm_lightweight_temp,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                extra_body=dict(settings.llm_lightweight_extra_body),
                max_model_len=settings.llm_lightweight_max_model_len,
            )
        if model_type == LLMModelProfile.EMBEDDING:
            return ModelConfig(
                model_name=settings.embedding_model_name,
                temperature=0.0,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                dimension=settings.embedding_dimension,
                distance_metric=settings.embedding_distance_metric,
                batch_max_texts=settings.embedding_batch_max_texts,
                max_model_len=settings.embedding_max_model_len,
            )
        raise ValueError(f"Unknown model_type: {model_type!r}")


def load_model_config_provider() -> ModelConfigProvider:
    """Load the configured model provider, falling back to environment settings."""
    provider_path = getenv("BY_QA_MODEL_CONFIG_PROVIDER", "").strip()
    if not provider_path:
        return EnvModelConfigProvider()

    module_name, separator, attribute_name = provider_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(
            "BY_QA_MODEL_CONFIG_PROVIDER must use the 'module:attribute' format."
        )

    module = import_module(module_name)
    provider_factory = getattr(module, attribute_name)
    provider = provider_factory() if callable(provider_factory) else provider_factory
    if not isinstance(provider, ModelConfigProvider):
        raise TypeError(
            "BY_QA_MODEL_CONFIG_PROVIDER must resolve to a ModelConfigProvider."
        )
    return provider


__all__ = [
    "EnvModelConfigProvider",
    "LLMModelProfile",
    "ModelConfig",
    "ModelConfigProvider",
    "load_model_config_provider",
]
