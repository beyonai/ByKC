"""Tests for compact state passed from retrieval workers to the parent graph."""

from langchain_core.messages import AIMessage, ToolMessage

from by_qa.qa.engines.instant.graph import compact_worker_output
from by_qa.qa.engines.instant.nodes.final_answer import final_answer_from_messages_node


def test_compact_worker_output_keeps_answer_and_source_metadata_only():
    result = compact_worker_output(
        {
            "messages": [
                ToolMessage(content="large body", tool_call_id="tool-call"),
                AIMessage(content="answer"),
            ],
            "retrieval_results": [{"content": "large body"}],
            "sub_answers": [
                {
                    "answer": "answer",
                    "confidence": 0.8,
                    "sources": [
                        {
                            "content": "large body",
                            "source": "doc.md",
                            "score": 0.8,
                        }
                    ],
                    "retrieval_results": [{"content": "large body"}],
                }
            ],
        }
    )

    assert result == {
        "sub_answers": [
            {
                "answer": "answer",
                "confidence": 0.8,
                "sources": [{"source": "doc.md", "score": 0.8}],
                "retrieval_results": [],
            }
        ]
    }


async def test_final_answer_prefers_compact_sub_answer():
    result = await final_answer_from_messages_node(
        {
            "sub_answers": [{"answer": "worker answer"}],
            "messages": [AIMessage(content="internal message")],
        }
    )

    assert result == {"final_answer": "worker answer"}
