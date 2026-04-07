"""Tests for QA LLM service compatibility behavior."""

from unittest.mock import AsyncMock, patch

import pytest

from by_qa.qa.services.llm_service import LLMService


def _mock_settings():
    settings = type("Settings", (), {})()
    settings.classifier_model = "classifier-model"
    settings.classifier_temp = 0.0
    settings.retrieval_model = "retrieval-model"
    settings.retrieval_temp = 0.1
    settings.generator_model = "generator-model"
    settings.generator_temp = 0.2
    settings.quality_model = "quality-model"
    settings.quality_temp = 0.3
    settings.llm_base_url = "https://example.com/v1"
    settings.llm_api_key = "secret"
    return settings


@pytest.mark.asyncio
async def test_generate_returns_error_string_when_model_call_fails():
    with patch(
        "by_qa.qa.services.llm_service.get_settings", return_value=_mock_settings()
    ):
        service = LLMService()

    with patch.object(
        service,
        "_get_streaming_model",
        return_value=type(
            "Model",
            (),
            {"ainvoke": AsyncMock(side_effect=RuntimeError("boom"))},
        )(),
    ):
        result = await service.generate([{"role": "user", "content": "hi"}])

    assert result == "Error: boom"


@pytest.mark.asyncio
async def test_generate_returns_json_error_when_json_mode_fails():
    with patch(
        "by_qa.qa.services.llm_service.get_settings", return_value=_mock_settings()
    ):
        service = LLMService()

    with patch.object(
        service,
        "_get_streaming_model",
        return_value=type(
            "Model",
            (),
            {"ainvoke": AsyncMock(side_effect=RuntimeError("boom"))},
        )(),
    ):
        result = await service.generate(
            [{"role": "user", "content": "hi"}], json_mode=True
        )

    assert result == '{"error": "LLM generation failed: boom"}'


@pytest.mark.asyncio
async def test_check_health_returns_unhealthy_payload_on_failure():
    with patch(
        "by_qa.qa.services.llm_service.get_settings", return_value=_mock_settings()
    ):
        service = LLMService()

    with patch.object(
        service,
        "_get_model",
        return_value=type(
            "Model",
            (),
            {"ainvoke": AsyncMock(side_effect=RuntimeError("down"))},
        )(),
    ):
        result = await service.check_health()

    assert result == {"status": "unhealthy", "error": "down"}
