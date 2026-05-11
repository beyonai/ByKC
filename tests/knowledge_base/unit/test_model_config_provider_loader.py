"""Tests for pluggable model config provider loading."""

from types import ModuleType, SimpleNamespace

import pytest

from by_qa.core.model_config import (
    EnvModelConfigProvider,
    LLMModelProfile,
    ModelConfig,
    load_model_config_provider,
)


class CustomProvider:
    """Small provider used to verify import-string loading."""

    async def get_config(self, model_type: str) -> ModelConfig:
        return ModelConfig(
            model_name=f"{model_type}-model",
            temperature=0.0,
            base_url="https://models.example.com/v1",
            api_key="secret",
        )


def test_load_model_config_provider_defaults_to_env_provider(monkeypatch):
    """The default service startup path should keep using env configuration."""
    monkeypatch.delenv("BY_QA_MODEL_CONFIG_PROVIDER", raising=False)

    provider = load_model_config_provider()

    assert isinstance(provider, EnvModelConfigProvider)


def test_load_model_config_provider_imports_configured_provider(monkeypatch):
    """A pip-installed provider should be loadable through a module path."""
    module = ModuleType("tests_custom_model_provider")
    module.CustomProvider = CustomProvider
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    monkeypatch.setenv(
        "BY_QA_MODEL_CONFIG_PROVIDER",
        "tests_custom_model_provider:CustomProvider",
    )

    provider = load_model_config_provider()

    assert isinstance(provider, CustomProvider)


def test_load_model_config_provider_rejects_invalid_provider_path(monkeypatch):
    """Invalid import strings should fail fast with a clear configuration error."""
    monkeypatch.setenv("BY_QA_MODEL_CONFIG_PROVIDER", "missing_separator")

    with pytest.raises(ValueError, match="BY_QA_MODEL_CONFIG_PROVIDER"):
        load_model_config_provider()


@pytest.mark.asyncio
async def test_env_model_config_provider_reads_standard_and_lightweight_profiles(
    monkeypatch,
):
    settings = SimpleNamespace(
        llm_base_url="https://models.example.com/v1",
        llm_api_key="secret",
        llm_standard_model="standard-model",
        llm_standard_temp=0.65,
        llm_standard_max_model_len=32000,
        llm_standard_extra_body={},
        llm_lightweight_model="lightweight-model",
        llm_lightweight_temp=0.15,
        llm_lightweight_max_model_len=16000,
        llm_lightweight_extra_body={"reasoning_effort": "low"},
        embedding_model_name="embedding-model",
        embedding_base_url="https://embed.example.com/v1",
        embedding_api_key="embed-secret",
        embedding_dimension=1024,
        embedding_distance_metric="cosine",
        embedding_batch_max_texts=32,
        embedding_max_model_len=8000,
    )
    monkeypatch.setattr("by_qa.core.model_config.get_settings", lambda: settings)
    provider = EnvModelConfigProvider()

    lightweight = await provider.get_config(LLMModelProfile.LIGHTWEIGHT)
    standard = await provider.get_config(LLMModelProfile.STANDARD)
    embedding = await provider.get_config(LLMModelProfile.EMBEDDING)

    assert lightweight.model_name == "lightweight-model"
    assert lightweight.temperature == 0.15
    assert lightweight.extra_body == {"reasoning_effort": "low"}
    assert lightweight.max_model_len == 16000

    assert standard.model_name == "standard-model"
    assert standard.temperature == 0.65
    assert standard.extra_body == {}
    assert standard.max_model_len == 32000
    assert embedding.model_name == "embedding-model"
    assert embedding.dimension == 1024
