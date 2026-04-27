"""Message utilities for distinguishing user input from agent-internal messages."""

from typing import Any

from langchain_core.messages import HumanMessage


def agent_metadata(name: str) -> dict:
    return {"source": name}


def is_user_message(msg: Any) -> bool:
    if isinstance(msg, HumanMessage):
        return msg.additional_kwargs.get("source") is None
    if isinstance(msg, dict):
        if msg.get("additional_kwargs", {}).get("source") is not None:
            return False
        return msg.get("role") == "user"
    return False


def extract_user_query_history(messages: list[Any], max_turns: int = 5) -> str:
    """Extract previous user inputs, excluding the current turn and agent-internal messages."""
    user_queries: list[str] = []
    first_user_found = False
    for msg in reversed(messages or []):
        if not is_user_message(msg):
            continue
        content = (
            msg.content if isinstance(msg, HumanMessage) else msg.get("content", "")
        )
        if not content:
            continue
        if not first_user_found:
            first_user_found = True
            continue
        user_queries.append(str(content))
        if len(user_queries) >= max_turns:
            break
    user_queries.reverse()
    return "\n".join(f"User: {q}" for q in user_queries)


__all__ = ["agent_metadata", "extract_user_query_history", "is_user_message"]
