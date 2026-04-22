"""Tests for parallel-safe retrieval index allocation."""

import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage

from by_qa.qa.instant.runtime.dispatcher import DispatcherToolMiddleware
from by_qa.qa.instant.runtime.operation_registry import (
    OPERATION_REGISTRY,
    OperationType,
)


def _make_request(*, state: dict, run_id: str, thread_id: str, tool_call_id: str):
    class FakeToolCall(dict):
        pass

    fake_execution_info = type("FakeExecutionInfo", (), {"run_id": run_id})()
    fake_runtime = type(
        "FakeRuntime",
        (),
        {
            "config": {
                "metadata": {"session_id": "session-1", "message_id": run_id},
                "configurable": {"thread_id": thread_id},
            },
            "execution_info": fake_execution_info,
        },
    )()

    class FakeRequest:
        tool_call = FakeToolCall(
            {
                "name": OPERATION_REGISTRY[OperationType.SEARCH].tool_name,
                "id": tool_call_id,
            }
        )
        runtime = fake_runtime

    FakeRequest.state = state
    return FakeRequest()


def _make_result(payload: list[dict]):
    class FakeToolMessage:
        content = json.dumps(payload, ensure_ascii=False)
        id = None

    return FakeToolMessage()


@pytest.mark.asyncio
async def test_dispatcher_allocates_contiguous_ids_for_single_request():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx,
        step,
        item_id: f"{sub_query_idx}-{step}-{item_id}",
        follow_up_prompt="继续",
    )
    request = _make_request(
        state={"sub_query_idx": 2, "current_step": 1},
        run_id="run-1",
        thread_id="thread-1",
        tool_call_id="tc-1",
    )

    cmd = await middleware._post_process_search(
        _make_result([{"content": "doc-a"}, {"content": "doc-b"}]),
        request,
    )

    assert cmd.update["retrieval_results"][0]["index_id"] == "2-1-1"
    assert cmd.update["retrieval_results"][1]["index_id"] == "2-1-2"
    tool_message = cmd.update["messages"][0]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.artifact[0]["index_id"] == "2-1-1"


@pytest.mark.asyncio
async def test_dispatcher_allocates_contiguous_ids_across_parallel_calls():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx,
        step,
        item_id: f"{sub_query_idx}-{step}-{item_id}",
        follow_up_prompt="继续",
    )
    state = {"sub_query_idx": 0, "current_step": 0}
    request_a = _make_request(
        state=state,
        run_id="run-1",
        thread_id="thread-1",
        tool_call_id="tc-a",
    )
    request_b = _make_request(
        state=state,
        run_id="run-1",
        thread_id="thread-1",
        tool_call_id="tc-b",
    )

    result_a, result_b = await asyncio.gather(
        middleware._post_process_search(
            _make_result([{"content": "doc-a"}, {"content": "doc-b"}]),
            request_a,
        ),
        middleware._post_process_search(
            _make_result([{"content": "doc-c"}]),
            request_b,
        ),
    )

    all_ids = sorted(
        [
            item["index_id"]
            for cmd in (result_a, result_b)
            for item in cmd.update["retrieval_results"]
        ],
        key=lambda value: int(value.rsplit("-", 1)[-1]),
    )
    assert all_ids == ["0-0-1", "0-0-2", "0-0-3"]


@pytest.mark.asyncio
async def test_dispatcher_resets_numbering_for_new_run_id_on_same_thread():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx,
        step,
        item_id: f"{sub_query_idx}-{step}-{item_id}",
        follow_up_prompt="继续",
    )
    state = {"sub_query_idx": 0, "current_step": 0}

    first = await middleware._post_process_search(
        _make_result([{"content": "doc-a"}]),
        _make_request(
            state=state,
            run_id="run-1",
            thread_id="thread-1",
            tool_call_id="tc-a",
        ),
    )
    second = await middleware._post_process_search(
        _make_result([{"content": "doc-b"}]),
        _make_request(
            state=state,
            run_id="run-2",
            thread_id="thread-1",
            tool_call_id="tc-b",
        ),
    )

    assert first.update["retrieval_results"][0]["index_id"] == "0-0-1"
    assert second.update["retrieval_results"][0]["index_id"] == "0-0-1"


@pytest.mark.asyncio
async def test_dispatcher_prefers_execution_info_run_id_over_session_id():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx,
        step,
        item_id: f"{sub_query_idx}-{step}-{item_id}",
        follow_up_prompt="继续",
    )
    state = {"sub_query_idx": 0, "current_step": 0}

    first = await middleware._post_process_search(
        _make_result([{"content": "doc-a"}]),
        _make_request(
            state=state,
            run_id="run-1",
            thread_id="thread-1",
            tool_call_id="tc-a",
        ),
    )
    second = await middleware._post_process_search(
        _make_result([{"content": "doc-b"}]),
        _make_request(
            state=state,
            run_id="run-2",
            thread_id="thread-1",
            tool_call_id="tc-b",
        ),
    )

    assert first.update["retrieval_results"][0]["index_id"] == "0-0-1"
    assert second.update["retrieval_results"][0]["index_id"] == "0-0-1"


@pytest.mark.asyncio
async def test_dispatcher_prefers_message_id_metadata_over_session_id():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx,
        step,
        item_id: f"{sub_query_idx}-{step}-{item_id}",
        follow_up_prompt="继续",
    )
    state = {"sub_query_idx": 0, "current_step": 0}

    first = await middleware._post_process_search(
        _make_result([{"content": "doc-a"}]),
        _make_request(
            state=state,
            run_id="msg-1",
            thread_id="thread-1",
            tool_call_id="tc-a",
        ),
    )
    second = await middleware._post_process_search(
        _make_result([{"content": "doc-b"}]),
        _make_request(
            state=state,
            run_id="msg-2",
            thread_id="thread-1",
            tool_call_id="tc-b",
        ),
    )

    assert first.update["retrieval_results"][0]["index_id"] == "0-0-1"
    assert second.update["retrieval_results"][0]["index_id"] == "0-0-1"


def test_dispatcher_index_id_fn_uses_sub_query_step_item_shape():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx,
        step,
        item_id: f"{sub_query_idx}-{step}-{item_id}",
        follow_up_prompt="继续",
    )
    assert middleware._index_id_fn(0, 2, 3) == "0-2-3"
