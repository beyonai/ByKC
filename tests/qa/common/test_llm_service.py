"""Tests for QA LLM service compatibility behavior."""

from unittest.mock import AsyncMock, patch

import pytest

from by_qa.core.exceptions import LLMGenerationError
from by_qa.core.model_config import ModelConfig
from by_qa.qa.services.llm_service import LLMService


def _mock_provider():
    async def get_config(model_type: str) -> ModelConfig:
        configs = {
            "classifier": ModelConfig(
                "classifier-model", 0.0, "https://example.com/v1", "secret"
            ),
            "retrieval": ModelConfig(
                "retrieval-model", 0.1, "https://example.com/v1", "secret"
            ),
            "generator": ModelConfig(
                "generator-model", 0.2, "https://example.com/v1", "secret"
            ),
            "quality": ModelConfig(
                "quality-model", 0.3, "https://example.com/v1", "secret"
            ),
        }
        return configs[model_type]

    provider = type("Provider", (), {"get_config": get_config})()
    return provider


def test_llm_service_defaults_to_configured_provider(monkeypatch):
    provider = _mock_provider()

    monkeypatch.setattr(
        "by_qa.qa.services.llm_service.load_model_config_provider",
        lambda: provider,
    )

    service = LLMService()

    assert service._provider is provider


def _mock_model(side_effect=None):
    return type(
        "Model",
        (),
        {"ainvoke": AsyncMock(side_effect=side_effect)},
    )()


@pytest.mark.asyncio
async def test_generate_raises_stable_error_when_model_call_fails():
    service = LLMService(provider=_mock_provider())

    with patch.object(
        service,
        "_get_streaming_model",
        new=AsyncMock(return_value=_mock_model(side_effect=RuntimeError("boom"))),
    ):
        with pytest.raises(LLMGenerationError) as exc_info:
            await service.generate([{"role": "user", "content": "hi"}])

    assert exc_info.value.message == "LLM generation failed"
    assert exc_info.value.details == {"error": "boom"}


@pytest.mark.asyncio
async def test_generate_raises_stable_error_when_json_mode_fails():
    service = LLMService(provider=_mock_provider())

    with patch.object(
        service,
        "_get_streaming_model",
        new=AsyncMock(return_value=_mock_model(side_effect=RuntimeError("boom"))),
    ):
        with pytest.raises(LLMGenerationError) as exc_info:
            await service.generate([{"role": "user", "content": "hi"}], json_mode=True)

    assert exc_info.value.message == "LLM generation failed"
    assert exc_info.value.details == {"error": "boom"}


@pytest.mark.asyncio
async def test_check_health_returns_unhealthy_payload_on_failure():
    service = LLMService(provider=_mock_provider())

    with patch.object(
        service,
        "_get_model",
        new=AsyncMock(return_value=_mock_model(side_effect=RuntimeError("down"))),
    ):
        result = await service.check_health()

    assert result == {"status": "unhealthy", "error": "down"}
