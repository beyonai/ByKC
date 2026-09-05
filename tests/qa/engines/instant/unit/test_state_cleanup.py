"""Tests for completed-turn checkpoint state cleanup."""

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.engines.instant.nodes.state_cleanup import cleanup_checkpoint_state_node


async def test_cleanup_retains_recent_user_history_and_drops_turn_artifacts():
    user_messages = [HumanMessage(content=f"question-{index}") for index in range(8)]
    result = await cleanup_checkpoint_state_node(
        {
            "messages": [
                user_messages[0],
                AIMessage(content="internal answer"),
                *user_messages[1:4],
                HumanMessage(
                    content="internal prompt",
                    additional_kwargs=agent_metadata("single_hop_entry"),
                ),
                ToolMessage(
                    content="large result",
                    artifact=[{"content": "large result"}],
                    tool_call_id="tool-call",
                ),
                *user_messages[4:],
            ],
            "retrieval_results": [{"content": "large result"}],
            "sub_answers": [{"answer": "answer"}],
        }
    )

    assert isinstance(result["messages"][0], RemoveMessage)
    assert result["messages"][0].id == REMOVE_ALL_MESSAGES
    assert result["messages"][1:] == user_messages[-6:]
    assert result["retrieval_results"] == {"mode": "RESET", "data": []}
    assert result["sub_answers"] == {"mode": "RESET", "data": []}


async def test_cleanup_handles_empty_state():
    result = await cleanup_checkpoint_state_node({})

    assert len(result["messages"]) == 1
    assert result["messages"][0].id == REMOVE_ALL_MESSAGES
