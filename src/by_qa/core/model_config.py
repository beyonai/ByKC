"""Pluggable model configuration interface for LLM and embedding services."""

from dataclasses import dataclass
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


@runtime_checkable
class ModelConfigProvider(Protocol):
    async def get_config(self, model_type: str) -> ModelConfig: ...


class EnvModelConfigProvider:
    """Default provider that reads model config from environment variables."""

    async def get_config(self, model_type: str) -> ModelConfig:
        settings = get_settings()
        llm_map = {
            "classifier": (settings.classifier_model, settings.classifier_temp),
            "retrieval": (settings.retrieval_model, settings.retrieval_temp),
            "generator": (settings.generator_model, settings.generator_temp),
            "quality": (settings.quality_model, settings.quality_temp),
            "decomposer": (settings.decomposer_model, settings.decomposer_temp),
            "aggregator": (settings.aggregator_model, settings.aggregator_temp),
        }
        if model_type in llm_map:
            model_name, temperature = llm_map[model_type]
            return ModelConfig(
                model_name=model_name,
                temperature=temperature,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
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
            )
        raise ValueError(f"Unknown model_type: {model_type!r}")


__all__ = ["EnvModelConfigProvider", "ModelConfig", "ModelConfigProvider"]
