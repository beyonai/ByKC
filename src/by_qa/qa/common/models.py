"""Shared Pydantic models for the QA domain."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class StreamEventType(str, Enum):
    """Event types for streaming QA responses."""

    NODE_START = "node_start"
    NODE_END = "node_end"
    TOKEN = "token"
    TOOL_CALL = "tool_call"
    TOOL_RESPONSE = "tool_response"
    ANSWER = "answer"
    DONE = "done"
    ERROR = "error"
    SEARCH_RESULT_CHUNKS = "search_result_chunks"
    DECOMPOSITION_COMPLETE = "decomposition_complete"
    ROUTING_DECISION = "routing_decision"
    SUBGRAPH_START = "subgraph_start"
    SUBGRAPH_END = "subgraph_end"
    SUB_ANSWER_GENERATED = "sub_answer_generated"
    HOP_START = "hop_start"
    HOP_END = "hop_end"
    INTERMEDIATE_ANSWER = "intermediate_answer"


class SearchFilters(BaseModel):
    """Search filters for refining search results."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    file_types: list[str] | None = None
    directories: list[str] | None = None
    exclude_patterns: list[str] | None = None
    max_results: int | None = Field(default=None, ge=1, le=100)
    min_relevance: float | None = Field(default=None, ge=0.0, le=1.0)


class SearchRequest(BaseModel):
    """Primary input model for QA search execution."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    query: str = Field(..., min_length=1)
    session_id: str | None = None
    message_id: str | None = None
    filters: SearchFilters | None = None
    stream: bool = False
    context: dict[str, Any] | None = None
    dataset_ids: list[int] | None = Field(default=None, alias="datasetIds")
    beyond_token: str | None = Field(default=None, alias="beyondToken")


CoreInput = SearchRequest


class CitationInfo(BaseModel):
    """Citation information for a source."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    chunk_id: str
    doc_name: str
    file_path: str
    section_title: str | None = None
    line_start: int
    line_end: int
    content_preview: str


class SearchResult(BaseModel):
    """Individual search result item."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    content: str
    source: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    """Primary output model for QA execution."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    answer: str
    session_id: str
    message_id: str | None = None
    citations: list[CitationInfo] = Field(default_factory=list)
    results: list[SearchResult] = Field(default_factory=list)
    query_type: Literal["simple", "complex"] = "simple"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] | None = None


CoreOutput = SearchResponse


class StreamEvent(BaseModel):
    """Single event in a streamed QA response."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    type: StreamEventType
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    role: str | None = None
    parent_ids: list[str] | None = None
    instance_id: str | None = None
    sub_query_id: str | None = None
    query_type: str | None = None
    hop_number: int | None = None
    routing_path: str | None = None

    @classmethod
    def node_start(
        cls,
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        return cls(
            type=StreamEventType.NODE_START,
            data=kwargs,
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )

    @classmethod
    def node_end(
        cls,
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        return cls(
            type=StreamEventType.NODE_END,
            data=kwargs,
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )

    @classmethod
    def token(
        cls,
        content: str,
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        return cls(
            type=StreamEventType.TOKEN,
            data={"content": content, **kwargs},
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )

    @classmethod
    def tool_call(
        cls,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_call_id: str | None = None,
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        data = {"toolName": tool_name, "toolInput": tool_input, **kwargs}
        if tool_call_id is not None:
            data["toolCallId"] = tool_call_id
        return cls(
            type=StreamEventType.TOOL_CALL,
            data=data,
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )

    @classmethod
    def tool_response(
        cls,
        tool_name: str,
        tool_output: dict[str, Any],
        tool_call_id: str | None = None,
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        data = {"toolName": tool_name, "toolOutput": tool_output, **kwargs}
        if tool_call_id is not None:
            data["toolCallId"] = tool_call_id
        return cls(
            type=StreamEventType.TOOL_RESPONSE,
            data=data,
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )

    @classmethod
    def answer(
        cls,
        content: str,
        citations: list[dict[str, Any]] | None = None,
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        return cls(
            type=StreamEventType.ANSWER,
            data={"content": content, "citations": citations or [], **kwargs},
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )

    @classmethod
    def done(
        cls,
        session_id: str,
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        return cls(
            type=StreamEventType.DONE,
            data={"sessionId": session_id, **kwargs},
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )

    @classmethod
    def error(
        cls,
        error: str,
        role: str | None = None,
        instance_id: str | None = None,
        **kwargs,
    ) -> "StreamEvent":
        return cls(
            type=StreamEventType.ERROR,
            data={"error": error, **kwargs},
            role=role,
            instance_id=instance_id,
        )

    @classmethod
    def search_result_chunks(
        cls,
        chunks: list[dict[str, Any]],
        role: str | None = None,
        instance_id: str | None = None,
        parent_ids: list[str] | None = None,
        **kwargs,
    ) -> "StreamEvent":
        return cls(
            type=StreamEventType.SEARCH_RESULT_CHUNKS,
            data={"chunks": chunks, **kwargs},
            role=role,
            instance_id=instance_id,
            parent_ids=parent_ids,
        )
