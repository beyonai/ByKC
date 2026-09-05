"""Checkpoint-state cleanup for completed instant-search turns."""

from typing import Any

from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from by_qa.qa.common.messages import is_user_message

MAX_RETAINED_USER_MESSAGES = 6


async def cleanup_checkpoint_state_node(state: dict[str, Any]) -> dict[str, Any]:
    """Keep conversation inputs while dropping large turn-local artifacts."""
    user_messages = [
        message for message in state.get("messages", []) if is_user_message(message)
    ][-MAX_RETAINED_USER_MESSAGES:]
    return {
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *user_messages],
        "retrieval_results": {"mode": "RESET", "data": []},
        "sub_answers": {"mode": "RESET", "data": []},
    }


__all__ = ["MAX_RETAINED_USER_MESSAGES", "cleanup_checkpoint_state_node"]
