"""Message utilities for distinguishing user input from agent-internal messages."""

from typing import Any

from langchain_core.messages import HumanMessage


def agent_metadata(name: str) -> dict:
    return {"source": name}


def get_message_source(msg: Any) -> str | None:
    """Return the source metadata of a message, or None if absent."""
    if isinstance(msg, HumanMessage):
        return msg.additional_kwargs.get("source")
    if isinstance(msg, dict):
        return msg.get("additional_kwargs", {}).get("source")
    return None


def is_user_message(msg: Any, *, include_sources: list[str] | None = None) -> bool:
    """Check whether a message represents user input.

    Args:
        msg: A HumanMessage or dict message.
        include_sources: Additional source names to treat as user-equivalent.
            Messages with these source values pass the check alongside real
            user messages (which have no source at all).
    """
    source = get_message_source(msg)
    if source is None:
        if isinstance(msg, HumanMessage):
            return True
        if isinstance(msg, dict):
            return msg.get("role") == "user"
        return False
    if include_sources and source in include_sources:
        return True
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


__all__ = [
    "agent_metadata",
    "extract_user_query_history",
    "get_message_source",
    "is_user_message",
]
