"""Tests for shared QA models and exceptions."""

from datetime import datetime

from by_qa.qa.common.exceptions import ConfigurationError
from by_qa.qa.common.models import SearchRequest, StreamEvent, StreamEventType


def test_stream_event_type_values_remain_stable():
    assert StreamEventType.NODE_START == "node_start"
    assert StreamEventType.NODE_END == "node_end"
    assert StreamEventType.TOKEN == "token"
    assert StreamEventType.ANSWER == "answer"
    assert StreamEventType.DONE == "done"
    assert StreamEventType.ERROR == "error"


def test_stream_event_done_uses_camel_case_payload():
    event = StreamEvent.done("session-123", total_tokens=42)
    payload = event.model_dump(by_alias=True)

    assert payload["data"]["sessionId"] == "session-123"
    assert payload["data"]["total_tokens"] == 42
    assert datetime.fromisoformat(payload["timestamp"])


def test_search_request_ignores_deprecated_dataset_and_beyond_fields():
    request = SearchRequest.model_validate(
        {
            "query": "员工请假制度",
            "datasetIds": [1, 2],
            "beyondToken": "token",
        }
    )
    payload = request.model_dump(by_alias=True, exclude_none=True)

    assert request.query == "员工请假制度"
    assert "datasetIds" not in payload
    assert "beyondToken" not in payload


def test_configuration_error_preserves_details():
    error = ConfigurationError("missing config", {"field": "OPENAI_API_KEY"})

    assert error.message == "missing config"
    assert error.details == {"field": "OPENAI_API_KEY"}
