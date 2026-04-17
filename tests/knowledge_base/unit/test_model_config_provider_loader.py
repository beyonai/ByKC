"""Tests for pluggable model config provider loading."""

from types import ModuleType

import pytest

from by_qa.core.model_config import (
    EnvModelConfigProvider,
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
