"""Final answer node for worker-based instant-search completion."""


async def final_answer_from_messages_node(state) -> dict:
    """Write the last worker-produced message content into final_answer."""
    sub_answers = state.get("sub_answers", [])
    if sub_answers:
        return {"final_answer": sub_answers[-1].get("answer", "")}

    messages = state.get("messages", [])
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content:
            return {"final_answer": content}
        if isinstance(message, dict) and message.get("content"):
            return {"final_answer": message["content"]}
    return {"final_answer": ""}
