"""Tests for by_qa.qa.common.messages utilities."""

from langchain_core.messages import AIMessage, HumanMessage

from by_qa.qa.common.messages import (
    agent_metadata,
    extract_user_query_history,
    is_user_message,
)


class TestAgentMetadata:
    def test_returns_source_key(self):
        assert agent_metadata("decomposer") == {"source": "decomposer"}

    def test_different_names(self):
        assert agent_metadata("rewriter")["source"] == "rewriter"
        assert agent_metadata("single_hop")["source"] == "single_hop"


class TestIsUserMessage:
    def test_plain_human_message_is_user(self):
        assert is_user_message(HumanMessage(content="hello")) is True

    def test_agent_human_message_is_not_user(self):
        msg = HumanMessage(content="hello", additional_kwargs={"source": "decomposer"})
        assert is_user_message(msg) is False

    def test_dict_user_role_is_user(self):
        assert is_user_message({"role": "user", "content": "hi"}) is True

    def test_dict_with_agent_source_is_not_user(self):
        msg = {
            "role": "user",
            "content": "hi",
            "additional_kwargs": {"source": "rewriter"},
        }
        assert is_user_message(msg) is False

    def test_dict_assistant_role_is_not_user(self):
        assert is_user_message({"role": "assistant", "content": "hi"}) is False

    def test_ai_message_is_not_user(self):
        assert is_user_message(AIMessage(content="hi")) is False

    def test_none_is_not_user(self):
        assert is_user_message(None) is False


class TestExtractUserQueryHistory:
    def test_empty_messages(self):
        assert extract_user_query_history([]) == ""
        assert extract_user_query_history(None) == ""

    def test_skips_current_turn(self):
        msgs = [
            HumanMessage(content="first"),
            HumanMessage(content="current"),
        ]
        assert extract_user_query_history(msgs) == "用户: first"

    def test_single_message_returns_empty(self):
        msgs = [HumanMessage(content="only one")]
        assert extract_user_query_history(msgs) == ""

    def test_filters_agent_messages(self):
        msgs = [
            HumanMessage(content="real user q1"),
            HumanMessage(
                content="agent internal", additional_kwargs={"source": "decomposer"}
            ),
            HumanMessage(content="real user q2"),
        ]
        result = extract_user_query_history(msgs)
        assert "real user q1" in result
        assert "agent internal" not in result

    def test_max_turns(self):
        msgs = [HumanMessage(content=f"q{i}") for i in range(10)]
        result = extract_user_query_history(msgs, max_turns=2)
        lines = result.strip().split("\n")
        assert len(lines) == 2

    def test_dict_messages(self):
        msgs = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "current"},
        ]
        result = extract_user_query_history(msgs)
        assert "old question" in result
        assert "current" not in result

    def test_mixed_agent_and_user_dict_messages(self):
        msgs = [
            {"role": "user", "content": "real"},
            {
                "role": "user",
                "content": "agent",
                "additional_kwargs": {"source": "rewriter"},
            },
            {"role": "user", "content": "current"},
        ]
        result = extract_user_query_history(msgs)
        assert "real" in result
        assert "agent" not in result
