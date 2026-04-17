"""Pluggable model configuration interface for LLM and embedding services."""

from dataclasses import dataclass
from importlib import import_module
from os import getenv
from typing import Protocol, runtime_checkable

from by_qa.config import get_settings


@dataclass
class ModelConfig:
    model_name: str
    temperature: float
    base_url: str
    api_key: str
    dimension: int | None = None
    distance_metric: str | None = None
    batch_max_texts: int | None = None
    max_model_len: int | None = None


@runtime_checkable
class ModelConfigProvider(Protocol):
    async def get_config(self, model_type: str) -> ModelConfig: ...


class EnvModelConfigProvider:
    """Default provider that reads model config from environment variables."""

    async def get_config(self, model_type: str) -> ModelConfig:
        settings = get_settings()
        llm_map = {
            "classifier": (
                settings.classifier_model,
                settings.classifier_temp,
                settings.classifier_max_model_len,
            ),
            "retrieval": (
                settings.retrieval_model,
                settings.retrieval_temp,
                settings.retrieval_max_model_len,
            ),
            "generator": (
                settings.generator_model,
                settings.generator_temp,
                settings.generator_max_model_len,
            ),
            "quality": (
                settings.quality_model,
                settings.quality_temp,
                settings.quality_max_model_len,
            ),
            "decomposer": (
                settings.decomposer_model,
                settings.decomposer_temp,
                settings.decomposer_max_model_len,
            ),
            "aggregator": (
                settings.aggregator_model,
                settings.aggregator_temp,
                settings.aggregator_max_model_len,
            ),
        }
        if model_type in llm_map:
            model_name, temperature, max_model_len = llm_map[model_type]
            return ModelConfig(
                model_name=model_name,
                temperature=temperature,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                max_model_len=max_model_len,
            )
        if model_type == "embedding":
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
    "ModelConfig",
    "ModelConfigProvider",
    "load_model_config_provider",
]
